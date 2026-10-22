#include "media/video_clip.h"
#include <stdexcept>
#include "control/protocol.h"

using namespace openscreen;
using namespace openscreen::cast;
using namespace std::chrono_literals;

namespace cast_to_tv {
namespace {
uint64_t Little(const uint8_t* bytes, size_t count) {
  uint64_t result = 0;
  for (size_t i = 0; i < count; ++i) result |= uint64_t(bytes[i]) << (8 * i);
  return result;
}
}  // namespace

Clip::Clip(const char* path, bool live) : file(path, std::ios::binary) {
  if (live) {
    std::string contents((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    desktop = std::make_unique<DesktopSource>(Parse(contents));
    width = desktop->width; height = desktop->height; rate = desktop->rate; scale = desktop->scale;
    return;
  }
  uint8_t header[32]{};
  if (!file.read(reinterpret_cast<char*>(header), sizeof(header)) ||
      std::string(reinterpret_cast<char*>(header), 4) != "DKIF" ||
      Little(header + 4, 2) != 0 || Little(header + 6, 2) != 32 ||
      std::string(reinterpret_cast<char*>(header + 8), 4) != "VP80")
    throw std::runtime_error("Expected a VP8 IVF clip");
  width = int(Little(header + 12, 2)); height = int(Little(header + 14, 2));
  rate = int(Little(header + 16, 4)); scale = int(Little(header + 20, 4));
  if (!width || !height || rate <= 0 || scale <= 0 || width > 3840 || height > 2160)
    throw std::runtime_error("Invalid IVF dimensions or timebase");
}

bool Clip::Next() {
  if (desktop) return desktop->Next(pts, data);
  uint8_t header[12]{};
  file.read(reinterpret_cast<char*>(header), sizeof(header));
  if (!file.gcount() && file.eof()) return false;
  if (file.gcount() != sizeof(header)) throw std::runtime_error("Truncated IVF frame header");
  auto length = Little(header, 4); auto next_pts = Little(header + 4, 8);
  if (!length || length > 4 * 1024 * 1024 || next_pts > uint64_t(rate) * 600 / scale ||
      (!data.empty() && next_pts <= pts)) throw std::runtime_error("Invalid IVF frame size/timestamp");
  pts = next_pts; data.resize(length);
  if (!file.read(reinterpret_cast<char*>(data.data()), data.size()))
    throw std::runtime_error("Truncated IVF frame");
  return true;
}

Clock::duration Clip::Offset() const { return Clock::duration(pts * 1000000ULL * scale / rate); }
}  // namespace cast_to_tv
