#pragma once
#include <string>
#include <vector>
#include "json/value.h"

namespace cast_to_tv {
std::vector<std::string> DesktopCommand(const Json::Value& config, int width, int height, int rate);
}  // namespace cast_to_tv
