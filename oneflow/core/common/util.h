#ifndef ONEFLOW_CORE_COMMON_UTIL_H_
#define ONEFLOW_CORE_COMMON_UTIL_H_

#include "oneflow/core/common/preprocessor.h"

#include <gflags/gflags.h>
#include <glog/logging.h>
#include <gtest/gtest.h>
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <forward_list>
#include <fstream>
#include <functional>
#include <iostream>
#include <list>
#include <memory>
#include <mutex>
#include <queue>
#include <random>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "oneflow/core/common/meta_util.hpp"

DECLARE_string(log_dir);

namespace std {
template<typename T0, typename T1>
struct hash<std::pair<T0, T1>> {
  std::size_t operator()(const std::pair<T0, T1>& p) const {
    auto h0 = std::hash<T0>{}(p.first);
    auto h1 = std::hash<T1>{}(p.second);
    return h0 ^ h1;
  }
};
}  // namespace std

namespace oneflow {

#define OF_DISALLOW_COPY(ClassName)     \
  ClassName(const ClassName&) = delete; \
  ClassName& operator=(const ClassName&) = delete;

#define OF_DISALLOW_MOVE(ClassName) \
  ClassName(ClassName&&) = delete;  \
  ClassName& operator=(ClassName&&) = delete;

#define OF_DISALLOW_COPY_AND_MOVE(ClassName) \
  OF_DISALLOW_COPY(ClassName)                \
  OF_DISALLOW_MOVE(ClassName)

#define UNIMPLEMENTED() LOG(FATAL) << "UNIMPLEMENTED"

#define TODO() LOG(FATAL) << "TODO"

template<typename T>
class Global final {
 public:
  static T* Get() { return *GetPPtr(); }
  static void SetAllocated(T* val) { *GetPPtr() = val; }
  template<typename... Args>
  static void New(Args&&... args) {
    CHECK(Get() == nullptr);
    LOG(INFO) << "NewGlobal " << typeid(T).name();
    *GetPPtr() = new T(std::forward<Args>(args)...);
  }
  static void Delete() {
    if (Get() != nullptr) {
      LOG(INFO) << "DeleteGlobal " << typeid(T).name();
      delete Get();
      *GetPPtr() = nullptr;
    }
  }

 private:
  static T** GetPPtr() {
    static T* ptr = nullptr;
    return &ptr;
  }
};

#define OF_COMMA ,

#define DEFINE_STATIC_VAR(type, name) \
  static type* name() {               \
    static type var;                  \
    return &var;                      \
  }

#define COMMAND(...)                                                \
  namespace {                                                       \
  struct OF_PP_CAT(CommandT, __LINE__) {                            \
    OF_PP_CAT(CommandT, __LINE__)() { __VA_ARGS__; }                \
  };                                                                \
  OF_PP_CAT(CommandT, __LINE__) OF_PP_CAT(g_command_var, __LINE__); \
  }

template<typename T>
bool operator==(const std::weak_ptr<T>& lhs, const std::weak_ptr<T>& rhs) {
  return lhs.lock().get() == rhs.lock().get();
}

template<typename Key, typename T, typename Hash = std::hash<Key>>
using HashMap = std::unordered_map<Key, T, Hash>;

template<typename Key, typename Hash = std::hash<Key>>
using HashSet = std::unordered_set<Key, Hash>;

template<typename T>
void SortAndRemoveDuplication(std::vector<T>* vec) {
  std::sort(vec->begin(), vec->end());
  auto unique_it = std::unique(vec->begin(), vec->end());
  vec->erase(unique_it, vec->end());
}

inline std::string NewUniqueId() {
  static int64_t id = 0;
  return std::to_string(id++);
}

inline const std::string& LogDir() {
  static std::string v = FLAGS_log_dir;
  return v;
}

template<typename K, typename V>
void EraseIf(HashMap<K, V>* hash_map, std::function<bool(typename HashMap<K, V>::iterator)> cond) {
  for (auto it = hash_map->begin(); it != hash_map->end();) {
    if (cond(it)) {
      hash_map->erase(it++);
    } else {
      ++it;
    }
  }
}

template<typename T>
typename std::enable_if<std::is_enum<T>::value, std::ostream&>::type operator<<(
    std::ostream& out_stream, const T& x) {
  out_stream << static_cast<int>(x);
  return out_stream;
}

template<typename OutType, typename InType>
OutType oneflow_cast(const InType&);

inline uint32_t NewRandomSeed() {
  static std::mt19937 gen{std::random_device{}()};
  return gen();
}

#if defined(WITH_CUDA)
#define DEVICE_TYPE_SEQ                  \
  OF_PP_MAKE_TUPLE_SEQ(DeviceType::kCPU) \
  OF_PP_MAKE_TUPLE_SEQ(DeviceType::kGPU)
#else
#define DEVICE_TYPE_SEQ OF_PP_MAKE_TUPLE_SEQ(DeviceType::kCPU)
#endif

#define DIM_SEQ (1)(2)(3)(4)(5)(6)(7)(8)

#define BOOL_SEQ (true)(false)
#define PARALLEL_POLICY_SEQ (ParallelPolicy::kModelParallel)(ParallelPolicy::kDataParallel)
#define ENCODE_CASE_SEQ                       \
  OF_PP_MAKE_TUPLE_SEQ(EncodeCase::kIdentity) \
  OF_PP_MAKE_TUPLE_SEQ(EncodeCase::kRaw)      \
  OF_PP_MAKE_TUPLE_SEQ(EncodeCase::kJpeg)
const std::string kOFRecordMapDefaultKey = "__kOFRecordMapDefaultKey__";

#define FOR_RANGE(type, i, begin, end) for (type i = (begin), __end = (end); i < __end; ++i)
#define FOR_EACH(it, container) for (auto it = container.begin(); it != container.end(); ++it)

void RedirectStdoutAndStderrToGlogDir();
void CloseStdoutAndStderr();

inline double GetCurTime() {
  return std::chrono::high_resolution_clock::now().time_since_epoch().count();
}

const size_t kCudaAlignSize = 8;
const size_t kCudaMemAllocAlignSize = 256;
inline size_t RoundUp(size_t n, size_t align) { return (n + align - 1) / align * align; }

size_t GetAvailableCpuMemSize();

template<typename T>
void Erase(T& container, const std::function<bool(const typename T::value_type&)>& NeedErase,
           const std::function<void(const typename T::value_type&)>& EraseElementHandler) {
  auto iter = container.begin();
  auto erase_from = container.end();
  while (iter != erase_from) {
    if (NeedErase(*iter)) {
      --erase_from;
      if (iter == erase_from) { break; }
      std::swap(*iter, *erase_from);
    } else {
      ++iter;
    }
  }
  for (; iter != container.end(); ++iter) { EraseElementHandler(*iter); }
  if (erase_from != container.end()) { container.erase(erase_from, container.end()); }
}

template<typename T>
void Erase(T& container, const std::function<bool(const typename T::value_type&)>& NeedErase) {
  Erase<T>(container, NeedErase, [](const typename T::value_type&) {});
}

template<typename T>
inline T MinVal() {
  return std::numeric_limits<T>::lowest();
}

template<typename T>
inline T MaxVal() {
  return std::numeric_limits<T>::max();
}

}  // namespace oneflow

#endif  // ONEFLOW_CORE_COMMON_UTIL_H_
