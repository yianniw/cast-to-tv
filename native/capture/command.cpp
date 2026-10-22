#include <windows.h>
#include <dxgi.h>
#include <stdexcept>
#include "capture/command.h"

namespace cast_to_tv {
namespace {
int DuplicationOutput(const Json::Value& display) {
  IDXGIFactory1* factory = nullptr;
  if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory))))
    throw std::runtime_error("Cannot enumerate Desktop Duplication outputs");
  IDXGIAdapter1* adapter = nullptr;
  HRESULT status = factory->EnumAdapters1(0, &adapter); factory->Release();
  if (FAILED(status)) throw std::runtime_error("No default graphics adapter");
  int match = -1;
  for (UINT i = 0; ; ++i) {
    IDXGIOutput* output = nullptr;
    if (FAILED(adapter->EnumOutputs(i, &output))) break;
    DXGI_OUTPUT_DESC desc{}; status = output->GetDesc(&desc); output->Release();
    if (FAILED(status) || !desc.AttachedToDesktop) continue;
    RECT rect = desc.DesktopCoordinates;
    if (rect.left == display["left"].asInt() && rect.top == display["top"].asInt() &&
        rect.right - rect.left == display["width"].asInt() &&
        rect.bottom - rect.top == display["height"].asInt()) { match = int(i); break; }
  }
  adapter->Release();
  if (match < 0) throw std::runtime_error("Selected monitor is not a Desktop Duplication output on the default adapter; select gdigrab capture");
  return match;
}
}  // namespace

std::vector<std::string> DesktopCommand(const Json::Value& config, int width, int height, int rate) {
  std::vector<std::string> args{config["ffmpeg"].asString(), "-hide_banner", "-loglevel", "warning", "-nostdin"};
  const auto& display = config["display"];
  auto number = [&](const char* key) { return std::to_string(display[key].asInt()); };
  if (config["capture"] == "ddagrab") {
    int output = DuplicationOutput(display);
    std::string source = "ddagrab=output_idx=" + std::to_string(output) + ":framerate=" + std::to_string(rate) +
                         ":draw_mouse=1,hwdownload,format=bgra";
    args.insert(args.end(), {"-f", "lavfi", "-i", source});
  } else if (config["capture"] == "gdigrab") {
    args.insert(args.end(), {"-f", "gdigrab", "-framerate", std::to_string(rate), "-draw_mouse", "1",
                            "-offset_x", number("left"), "-offset_y", number("top"), "-video_size",
                            number("width") + "x" + number("height"), "-i", "desktop"});
  } else throw std::runtime_error("Unknown desktop capture mode");
  if (config.isMember("duration")) args.insert(args.end(), {"-t", config["duration"].asString()});
  args.insert(args.end(), {"-vf",
                          "scale=" + std::to_string(width) + ":" + std::to_string(height) + ",format=yuv420p",
                          "-an", "-c:v", "libvpx", "-deadline", "realtime", "-cpu-used", "6", "-threads", "8",
                          "-b:v", "8M", "-crf", "10", "-g", "15", "-keyint_min", "15",
                          "-error-resilient", "1", "-lag-in-frames", "0", "-auto-alt-ref", "0",
                          "-flush_packets", "1", "-f", "ivf", "pipe:1"});
  return args;
}
}  // namespace cast_to_tv
