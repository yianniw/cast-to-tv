#pragma once
#include <cstdint>
#include <fstream>
#include <memory>
#include <vector>
#include "desktop_source.h"

namespace cast_to_tv {
// Supplies encoded VP8 frames from an IVF file or live desktop capture.
struct Clip {
  std::ifstream file;
  int width, height, rate, scale;
  uint64_t pts = 0;
  std::vector<uint8_t> data;
  std::unique_ptr<DesktopSource> desktop;
  explicit Clip(const char* path, bool live = false);
  bool Next();
  openscreen::Clock::duration Offset() const;
};
}  // namespace cast_to_tv
