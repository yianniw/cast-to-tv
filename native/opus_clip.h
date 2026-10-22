#pragma once
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

// Minimal reader for locally generated, single-stream, stereo Ogg Opus clips.
// Extract raw 20 ms packets; container headers are not sent as audio frames.
class OpusClip {
 public:
  explicit OpusClip(const char* path) {
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("Cannot open synthetic Opus clip");
    uint32_t serial = 0, sequence = 0; size_t packet_number = 0, total_bytes = 0;
    std::vector<uint8_t> pending;
    bool first_page = true, ended = false;
    for (;;) {
      uint8_t header[27]{};
      file.read(reinterpret_cast<char*>(header), 27);
      if (!file.gcount() && file.eof()) break;
      if (file.gcount() != 27 || std::string(reinterpret_cast<char*>(header), 4) != "OggS" || header[4] != 0)
        throw std::runtime_error("Invalid Ogg page");
      auto read32 = [](const uint8_t* data) {
        return uint32_t(data[0]) | uint32_t(data[1]) << 8 | uint32_t(data[2]) << 16 | uint32_t(data[3]) << 24;
      };
      if (first_page) serial = read32(header + 14);
      if (ended || read32(header + 14) != serial || read32(header + 18) != sequence++ ||
          bool(header[5] & 1) != !pending.empty() || (first_page && !(header[5] & 2)))
        throw std::runtime_error("Unsupported chained/discontinuous Ogg stream");
      first_page = false; ended = bool(header[5] & 4);
      std::vector<uint8_t> lacing(header[26]);
      if (!file.read(reinterpret_cast<char*>(lacing.data()), lacing.size())) throw std::runtime_error("Truncated Ogg lacing");
      for (uint8_t length : lacing) {
        size_t offset = pending.size(); pending.resize(offset + length);
        total_bytes += length;
        if (pending.size() > 65536 || total_bytes > 16 * 1024 * 1024)
          throw std::runtime_error("Synthetic Ogg clip exceeds limits");
        if (!file.read(reinterpret_cast<char*>(pending.data() + offset), length)) throw std::runtime_error("Truncated Ogg packet");
        if (length == 255) continue;
        if (packet_number == 0) {
          if (pending.size() != 19 || std::string(reinterpret_cast<char*>(pending.data()), 8) != "OpusHead" ||
              pending[8] != 1 || pending[9] != 2 || pending[18] != 0)
            throw std::runtime_error("Expected stereo Opus mapping family zero");
        } else if (packet_number == 1) {
          if (pending.size() < 8 || std::string(reinterpret_cast<char*>(pending.data()), 8) != "OpusTags")
            throw std::runtime_error("Missing OpusTags");
        } else {
          if (pending.empty() || pending.size() > 4000 || packets.size() >= 30001)
            throw std::runtime_error("Invalid synthetic Opus packet");
          int config = pending[0] >> 3;
          int samples = config >= 16 ? 120 << (config & 3) :
                        config >= 12 ? 480 << (config & 1) :
                        (config & 3) == 3 ? 2880 : 480 << (config & 3);
          int code = pending[0] & 3;
          int frames = code == 0 ? 1 : code < 3 ? 2 : pending.size() > 1 ? pending[1] & 63 : 0;
          if (samples * frames != 960) throw std::runtime_error("Synthetic audio requires 20 ms Opus packets");
          packets.push_back(std::move(pending));
        }
        pending.clear(); ++packet_number;
      }
    }
    if (!ended || !pending.empty() || packets.empty()) throw std::runtime_error("Incomplete synthetic Ogg clip");
  }
  std::vector<std::vector<uint8_t>> packets;
};
