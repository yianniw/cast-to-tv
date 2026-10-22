#pragma once
#include <windows.h>
#include <atomic>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include "platform/api/time.h"

struct AudioPacket {
  uint64_t samples = 0;
  openscreen::Clock::time_point reference;
  std::vector<uint8_t> data;
};
class SystemAudio {
 public:
  SystemAudio();
  ~SystemAudio();
  void Start();
  bool Next(AudioPacket& packet);
  uint64_t Replaced() const { return replaced_; }
  uint64_t Discontinuities() const { return discontinuities_; }
 private:
  void Capture();
  HANDLE stop_ = nullptr;
  std::thread worker_;
  std::mutex mutex_;
  std::deque<AudioPacket> packets_;
  std::string error_;
  std::atomic<uint64_t> replaced_{0}, discontinuities_{0};
};
