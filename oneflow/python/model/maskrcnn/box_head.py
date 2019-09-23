from matcher import Matcher


class BoxHead(object):
    def __init__(self, cfg):
        self.cfg = cfg

    # proposals: list of [R, 4] wrt. images
    # gt_boxes_list: list of [G, 4] wrt. images
    # gt_labels_list: list of [G] wrt. images
    # features: list of [N, C_i, H_i, W_i] wrt. fpn layers
    def build_train(self, proposals, gt_boxes_list, gt_labels_list, features):
        with flow.deprecated.variable_scope("roi"):
            label_list = []
            proposal_list = []
            bbox_target_list = []
            for img_idx in range(len(proposals)):
                with flow.deprecated.variable_scope("matcher"):
                    box_head_matcher = Matcher(
                        self.cfg.BOX_HEAD.FOREGROUND_THRESHOLD,
                        self.cfg.BOX_HEAD.BACKGROUND_THRESHOLD_HIGH,
                    )
                    matched_indices = box_head_matcher.build(
                        proposals[img_idx],
                        gt_boxes_list[img_idx],
                        allow_low_quality_matches=False,
                    )
                pos_inds = flow.squeeze(
                    flow.local_nonzero(
                        matched_indices >= flow.constant_scalar(0, flow.int32)
                    ),
                    axis=[1],
                )
                neg_inds = flow.squeeze(
                    flow.local_nonzero(
                        matched_indices == flow.constant_scalar(-1, flow.int32)
                    ),
                    axis=[1],
                )
                sampled_pos_inds, sampled_neg_inds = flow.detection.pos_neg_sampler(
                    pos_inds,
                    neg_inds,
                    total_subsample_num=self.cfg.BOX_HEAD.NUM_SAMPLED_ROI_PER_IMG,
                    pos_fraction=self.cfg.BOX_HEAD.FOREGROUND_FRACTION,
                )
                sampled_pos_neg_inds = flow.concat(
                    [sampled_pos_inds, sampled_neg_inds], axis=0
                )

                clamped_matched_indices = flow.clip_by_value(matched_indices, 0)
                proposal_gt_labels = flow.local_gather(
                    gt_labels_list[img_idx], clamped_matched_indices
                )
                proposal_gt_labels = flow.local_scatter_nd_update(
                    proposal_gt_labels,
                    flow.expand_dims(sampled_neg_inds, axis=1),
                    flow.constant_like(sampled_neg_inds, int(0)),
                )
                matched_gt_label = flow.local_gather(
                    proposal_gt_labels, sampled_pos_neg_inds
                )
                label_list.append(matched_gt_label)

                gt_indices = flow.local_gather(
                    clamped_matched_indices, sampled_pos_neg_inds
                )
                proposal_per_img = flow.local_gather(
                    proposals[img_idx], sampled_pos_neg_inds
                )
                gt_boxes_per_img = flow.local_gather(gt_boxes_list[img_idx], gt_indices)
                bbox_target_list.append(
                    self.box_encode(
                        gt_boxes_per_img,
                        proposal_per_img,
                        regression_weights={
                            "weight_x": self.cfg.BOX_HEAD.WEIGHT_X,
                            "weight_y": self.cfg.BOX_HEAD.WEIGHT_Y,
                            "weight_h": self.cfg.BOX_HEAD.WEIGHT_H,
                            "weight_w": self.cfg.BOX_HEAD.WEIGHT_W,
                        },
                    )
                )
                proposal_list.append(proposal_per_img)

            proposals = flow.concat(proposal_list, axis=0)
            img_ids = flow.concat(self.extract_piece_slice_id(proposal_list), axis=0)
            labels = flow.concat(label_list, axis=0)
            bbox_targets = flow.concat(bbox_target_list, axis=0)

            # box feature extractor
            x = self.box_feature_extractor(proposals, img_ids, features)

            # box predictor
            bbox_regression, cls_logits = self.box_predictor(x)

            # construct cls loss
            total_elem_cnt = flow.math.elem_cnt(labels)
            box_head_cls_loss = flow.div(
                flow.math.reduce_sum(
                    self.dl_net.SparseCrossEntropy(
                        flow.nn.softmax(cls_logits), labels, name="sparse_cross_entropy"
                    )
                ),
                total_elem_cnt,
            )

            # construct bbox loss
            total_pos_inds = flow.squeeze(
                flow.local_nonzero(labels != flow.constant_scalar(int(0), flow.int32)),
                axis=[1],
            )
            # [R, 81, 4]
            pos_bbox_reg = flow.reshape(
                flow.local_gather(bbox_regression, total_pos_inds),
                shape={"dim": [-1, 4]},
                has_dim0_in_shape=False,
            )
            # [R, 1]
            indices = flow.expand_dims(
                flow.local_gather(labels, total_pos_inds), axis=1
            )
            bbox_pred = flow.squeeze(
                flow.gather(params=pos_bbox_reg, indices=indices, batch_dims=1),
                axis=[1],
            )
            bbox_target = flow.local_gather(bbox_targets, total_pos_inds)
            box_head_box_loss = flow.div(
                flow.math.reduce_sum(flow.detection.smooth_l1(bbox_pred, bbox_target)),
                total_elem_cnt,
            )

            return box_head_box_loss, box_head_cls_loss

    def box_feature_extractor(self, proposals, img_ids, features):
        levels = self.level_map(proposals)
        print("box_head levels: " + levels.op_name())
        level_idx_2 = flow.local_nonzero(
            levels == flow.constant_scalar(int(0), flow.int32)
        )
        level_idx_3 = flow.local_nonzero(
            levels == flow.constant_scalar(int(1), flow.int32)
        )
        level_idx_4 = flow.local_nonzero(
            levels == flow.constant_scalar(int(2), flow.int32)
        )
        level_idx_5 = flow.local_nonzero(
            levels == flow.constant_scalar(int(3), flow.int32)
        )
        proposals_with_img_ids = flow.concat(
            [flow.expand_dims(flow.cast(img_ids, flow.float), 1), proposals], axis=1
        )
        roi_features_0 = flow.detection.roi_align(
            in_blob=features[0],
            rois=flow.local_gather(
                proposals_with_img_ids, flow.squeeze(level_idx_2, axis=[1])
            ),
            pooled_h=self.cfg.BOX_HEAD.POOLED_H,
            pooled_w=self.cfg.BOX_HEAD.POOLED_W,
            spatial_scale=self.cfg.BOX_HEAD.SPATIAL_SCALE / pow(2, 0),
            sampling_ratio=self.cfg.BOX_HEAD.SAMPLING_RATIO,
        )
        roi_features_1 = flow.detection.roi_align(
            in_blob=features[1],
            rois=flow.local_gather(
                proposals_with_img_ids, flow.squeeze(level_idx_3, axis=[1])
            ),
            pooled_h=self.cfg.BOX_HEAD.POOLED_H,
            pooled_w=self.cfg.BOX_HEAD.POOLED_W,
            spatial_scale=self.cfg.BOX_HEAD.SPATIAL_SCALE / pow(2, 1),
            sampling_ratio=self.cfg.BOX_HEAD.SAMPLING_RATIO,
        )
        roi_features_2 = flow.detection.roi_align(
            in_blob=features[2],
            rois=flow.local_gather(
                proposals_with_img_ids, flow.squeeze(level_idx_4, axis=[1])
            ),
            pooled_h=self.cfg.BOX_HEAD.POOLED_H,
            pooled_w=self.cfg.BOX_HEAD.POOLED_W,
            spatial_scale=self.cfg.BOX_HEAD.SPATIAL_SCALE / pow(2, 2),
            sampling_ratio=self.cfg.BOX_HEAD.SAMPLING_RATIO,
        )
        roi_features_3 = flow.detection.roi_align(
            in_blob=features[3],
            rois=flow.local_gather(
                proposals_with_img_ids, flow.squeeze(level_idx_5, axis=[1])
            ),
            pooled_h=self.cfg.BOX_HEAD.POOLED_H,
            pooled_w=self.cfg.BOX_HEAD.POOLED_W,
            spatial_scale=self.cfg.BOX_HEAD.SPATIAL_SCALE / pow(2, 3),
            sampling_ratio=self.cfg.BOX_HEAD.SAMPLING_RATIO,
        )
        roi_features = flow.concat(
            [roi_features_0, roi_features_1, roi_features_2, roi_features_3], axis=0
        )
        origin_indices = flow.concat(
            [level_idx_2, level_idx_3, level_idx_4, level_idx_5], axis=0
        )
        roi_features = flow.local_scatter_nd_update(
            flow.constant_like(roi_features, 0), origin_indices, roi_features
        )
        x = flow.layers.dense(
            inputs=roi_features,
            units=1024,
            activation=flow.keras.activations.relu,
            use_bias=True,
            name="fc6",
        )
        x = flow.layers.dense(
            inputs=x,
            units=1024,
            activation=flow.keras.activations.relu,
            use_bias=True,
            name="fc7",
        )

        return x

    def box_predictor(self, x):
        bbox_regression = flow.layers.dense(
            inputs=x,
            units=self.cfg.BOX_HEAD.NUM_CLASSES * 4,
            activation=None,
            use_bias=True,
            name="bbox_pred",
        )
        cls_logits = flow.layers.dense(
            inputs=x,
            uniti=self.cfg.BOX_HEAD.NUM_CLASSES,
            activation=None,
            use_bias=True,
            name="cls_score",
        )

        return bbox_regression, cls_logits