#pragma once
#include <memory>
#include <string>
#include "windows_platform.h"
#include "media/video_clip.h"
#include "opus_clip.h"
#include "system_audio.h"
// Windows macros must not rename Open Screen's virtual methods.
#ifdef SendMessage
#undef SendMessage
#endif
#ifdef PostMessage
#undef PostMessage
#endif
#include "cast/streaming/public/sender_session.h"

namespace cast_to_tv {
using openscreen::Clock;
using openscreen::Error;
using openscreen::cast::FrameId;
using openscreen::cast::Sender;
using openscreen::cast::SenderSession;
namespace capture_recommendations = openscreen::cast::capture_recommendations;

// Owns negotiated senders, pacing, frame dependencies, and sender statistics.
class Probe final : public SenderSession::Client, public Sender::Observer {
 public:
  Probe(WindowsTaskRunner& runner, Clip& clip, OpusClip* audio = nullptr, SystemAudio* system_audio = nullptr);
  void OnNegotiated(const SenderSession*, SenderSession::ConfiguredSenders senders,
                    capture_recommendations::Recommendations recommendations) override;
  void OnError(const SenderSession*, const Error& error) override;
  void OnFrameCanceled(FrameId) override;
  void OnPictureLost() override;
  void OnPacketsRetransmitted(int count) override;
  void Fail(const std::string& message);
  void Stop();
  void ReleaseSender();
  int result = 0;
 private:
  void Stats(const char* kind);
  void AudioTick();
  void Tick();
  WindowsTaskRunner& runner_; Clip& clip_;
  std::unique_ptr<Sender> sender_;
  OpusClip* audio_;
  SystemAudio* system_audio_;
  std::unique_ptr<Sender> audio_sender_;
  Clock::time_point audio_origin_;
  uint64_t audio_index_ = 0, audio_sent_ = 0, audio_dropped_ = 0;
  Clock::time_point origin_;
  Clock::time_point waiting_since_;
  bool loaded_ = false;
  bool references_valid_ = false, have_previous_pts_ = false;
  uint64_t previous_pts_ = 0;
  FrameId previous_frame_;
  uint64_t late_drops_ = 0, dependency_drops_ = 0, keyframe_request_drops_ = 0, enqueue_rejections_ = 0;
  double last_frame_age_ms_ = 0;
  int last_enqueue_result_ = 0;
  uint64_t frames_ = 0, sent_ = 0, released_ = 0, dropped_ = 0, picture_losses_ = 0, retransmissions_ = 0;
};
}  // namespace cast_to_tv
