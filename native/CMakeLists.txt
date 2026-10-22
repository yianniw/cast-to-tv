cmake_minimum_required(VERSION 3.25)
project(CastStreamingProbe LANGUAGES C CXX)
if(NOT WIN32)
  message(FATAL_ERROR "This probe supplies a Windows platform adapter")
endif()
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(OPENSCREEN_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.tmp/native-build/openscreen" CACHE PATH "Pinned Open Screen checkout")
get_filename_component(OS "${OPENSCREEN_ROOT}" ABSOLUTE)
add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(ABSL_PROPAGATE_CXX_STD ON CACHE BOOL "" FORCE)
set(ABSL_ENABLE_INSTALL OFF CACHE BOOL "" FORCE)
set(ABSL_MSVC_STATIC_RUNTIME ON CACHE BOOL "" FORCE)
add_subdirectory("${OS}/third_party/abseil/src" abseil EXCLUDE_FROM_ALL)
set(protobuf_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(protobuf_INSTALL OFF CACHE BOOL "" FORCE)
set(protobuf_BUILD_LIBUPB ON CACHE BOOL "" FORCE)
set(protobuf_LOCAL_DEPENDENCIES_ONLY ON CACHE BOOL "" FORCE)
set(protobuf_MSVC_STATIC_RUNTIME ON CACHE BOOL "" FORCE)
add_subdirectory("${OS}/third_party/protobuf" protobuf EXCLUDE_FROM_ALL)
set(OPENSSL_NO_ASM ON CACHE BOOL "" FORCE)
add_subdirectory("${OS}/third_party/boringssl/src" boringssl EXCLUDE_FROM_ALL)
set(JSONCPP_WITH_TESTS OFF CACHE BOOL "" FORCE)
set(JSONCPP_WITH_POST_BUILD_UNITTEST OFF CACHE BOOL "" FORCE)
set(JSONCPP_STATIC_WINDOWS_RUNTIME ON CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_OBJECT_LIBS OFF CACHE BOOL "" FORCE)
add_subdirectory("${OS}/third_party/jsoncpp/src" jsoncpp EXCLUDE_FROM_ALL)
set(OPUS_STATIC_RUNTIME ON CACHE BOOL "" FORCE)
set(OPUS_BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(OPUS_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
add_subdirectory("${OS}/third_party/opus/src" opus EXCLUDE_FROM_ALL)

# Mirror just the pinned GN targets used by SenderSession, with the C++ parsers.
# Excluding POSIX standalone code is deliberate: windows_platform.cpp implements
# Open Screen's embedding API. No receiver, DNS discovery, or TLS port is needed.
function(gn_sources directory target output)
  file(READ "${OS}/${directory}/BUILD.gn" contents)
  string(FIND "${contents}" "openscreen_source_set(\"${target}\")" start)
  if(start LESS 0)
    message(FATAL_ERROR "Missing pinned GN target ${directory}:${target}")
  endif()
  string(SUBSTRING "${contents}" ${start} -1 contents)
  string(REGEX MATCH "sources = \\[[^]]*\\]" block "${contents}")
  string(REGEX MATCHALL "\"[^\"]+\\.cc\"" files "${block}")
  set(result)
  foreach(source IN LISTS files)
    string(REPLACE "\"" "" source "${source}")
    list(APPEND result "${OS}/${directory}/${source}")
  endforeach()
  set(${output} ${result} PARENT_SCOPE)
endfunction()
gn_sources(platform base base_sources)
gn_sources(platform api api_sources)
gn_sources(util util util_sources)
gn_sources(cast/streaming streaming_configs config_sources)
gn_sources(cast/streaming common common_sources)
gn_sources(cast/streaming sender sender_sources)
# Expose the upstream SessionConfig in-flight override through SenderSession's
# embedding configuration. Keep pristine dependency sources and generate the
# two small additions in the build tree, with exact pinned-context checks.
file(READ "${OS}/cast/streaming/public/sender_session.h" session_header)
file(READ "${OS}/cast/streaming/public/sender_session.cc" session_source)
set(header_anchor "    std::optional<size_t> udp_receive_buffer_size;")
set(source_anchor "  return std::make_unique<SenderImpl>(*config_.environment, packet_router_,")
string(FIND "${session_header}" "${header_anchor}" header_position)
string(FIND "${session_source}" "${source_anchor}" source_position)
if(header_position LESS 0 OR source_position LESS 0)
  message(FATAL_ERROR "Pinned SenderSession overlay context changed")
endif()
string(REPLACE "${header_anchor}"
  "    std::optional<std::chrono::milliseconds> max_in_flight_media_duration;\n${header_anchor}"
  session_header "${session_header}")
string(REPLACE "${source_anchor}"
  "  config.max_in_flight_media_duration = config_.max_in_flight_media_duration;\n${source_anchor}"
  session_source "${session_source}")
set(overlay "${CMAKE_CURRENT_BINARY_DIR}/overlay")
file(MAKE_DIRECTORY "${overlay}/cast/streaming/public")
file(WRITE "${overlay}/cast/streaming/public/sender_session.h" "${session_header}")
file(WRITE "${overlay}/cast/streaming/public/sender_session.cc" "${session_source}")
list(REMOVE_ITEM sender_sources "${OS}/cast/streaming/public/sender_session.cc")
list(APPEND sender_sources "${overlay}/cast/streaming/public/sender_session.cc")
set(proto_sources)
foreach(proto input remoting sender_stats)
  set(stem "cast/streaming/${proto}")
  add_custom_command(OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/${stem}.pb.cc" "${CMAKE_CURRENT_BINARY_DIR}/${stem}.pb.h"
    COMMAND protobuf::protoc --cpp_out=${CMAKE_CURRENT_BINARY_DIR} --proto_path=${OS} "${OS}/${stem}.proto"
    DEPENDS protobuf::protoc "${OS}/${stem}.proto" VERBATIM)
  list(APPEND proto_sources "${CMAKE_CURRENT_BINARY_DIR}/${stem}.pb.cc")
endforeach()
add_library(openscreen_sender STATIC ${base_sources} ${api_sources} ${util_sources}
  ${config_sources} ${common_sources} ${sender_sources} ${proto_sources}
  "${OS}/util/json/json_serialization_jsoncpp.cc"
  "${OS}/third_party/modp_b64/modp_b64.cc")
target_include_directories(openscreen_sender PUBLIC "${overlay}" "${OS}" "${CMAKE_CURRENT_BINARY_DIR}")
if(MSVC)
  # Open Screen's OpenSSL error macro uses Clang/GCC's spelling.
  target_compile_definitions(openscreen_sender PUBLIC __PRETTY_FUNCTION__=__FUNCSIG__)
endif()
target_link_libraries(openscreen_sender PUBLIC protobuf::libprotobuf ssl crypto jsoncpp_static ws2_32)
add_executable(cast_streaming_probe
  probe.cpp
  windows_platform.cpp
  platform/task_runner.cpp
  platform/udp_socket.cpp
  desktop_source.cpp
  capture/command.cpp
  system_audio.cpp
  media/video_clip.cpp
  control/protocol.cpp
  control/input.cpp
  sender/probe.cpp)
target_include_directories(cast_streaming_probe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}")
target_link_libraries(cast_streaming_probe PRIVATE openscreen_sender opus dxgi ole32 uuid)
