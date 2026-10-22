#include "sender/probe.h"
#include "control/protocol.h"
#include <sstream>
#include <stdexcept>

using namespace openscreen;
using namespace openscreen::cast;
using namespace std::chrono_literals;

namespace cast_to_tv {
Probe::Probe(WindowsTaskRunner& runner, Clip& clip, OpusClip* audio, SystemAudio* system_audio)
    : runner_(runner), clip_(clip), audio_(audio), system_audio_(system_audio) {}

void Probe::OnNegotiated(const SenderSession*, SenderSession::ConfiguredSenders senders,
                         capture_recommendations::Recommendations recommendations) {
  sender_ = std::move(senders.video_sender);
  audio_sender_ = std::move(senders.audio_sender);
  if (!sender_ || senders.video_config.codec != VideoCodec::kVp8) { Fail("VP8 video was not selected"); return; }
  if ((audio_ || system_audio_) && (!audio_sender_ || senders.audio_config.codec != AudioCodec::kOpus)) {
    Fail("Opus audio was not selected"); return;
  }
  sender_->SetObserver(this);
  Json::Value event; event["event"] = "negotiated";
  event["playout_delay_ms"] = int(sender_->config().target_playout_delay.count());
  event["receiver_max_width"] = recommendations.video.maximum.width;
  event["receiver_max_height"] = recommendations.video.maximum.height;
  event["audio"] = system_audio_ ? "system Opus" : audio_ ? "synthetic Opus" : "none";
  Emit(event);
  if (clip_.desktop) clip_.desktop->Start();
  if (system_audio_) system_audio_->Start();
  waiting_since_ = Clock::now();
  origin_ = Clock::now() + 100ms;
  audio_origin_ = origin_;
  runner_.PostTaskWithDelay([this] { Tick(); }, 100ms);
  if (audio_ || system_audio_) runner_.PostTaskWithDelay([this] { AudioTick(); }, 100ms);
}

void Probe::OnError(const SenderSession*, const Error& error) {
  std::stringstream message; message << error; Fail(message.str());
}

void Probe::OnFrameCanceled(FrameId) { ++released_; }

void Probe::OnPictureLost() { ++picture_losses_; references_valid_ = false; }

void Probe::OnPacketsRetransmitted(int count) { retransmissions_ += count; }

void Probe::Fail(const std::string& message) {
  Json::Value event; event["event"] = "error"; event["message"] = message; Emit(event);
  result = 1; runner_.Stop();
}

void Probe::Stop() { runner_.Stop(); }

void Probe::ReleaseSender() { audio_sender_.reset(); sender_.reset(); }

void Probe::Stats(const char* kind) {
  Json::Value event; event["event"] = kind; event["sent"] = Json::UInt64(sent_);
  event["released"] = Json::UInt64(released_); event["dropped"] = Json::UInt64(dropped_);
  event["picture_losses"] = Json::UInt64(picture_losses_);
  event["retransmissions"] = Json::UInt64(retransmissions_);
  event["in_flight"] = Json::UInt64(sender_->GetInFlightFrameCount());
  event["round_trip_ms"] = sender_->GetCurrentRoundTripTime().count() / 1000.0;
  event["media_seconds"] = clip_.Offset().count() / 1000000.0;
  event["late_drops"] = Json::UInt64(late_drops_);
  event["dependency_drops"] = Json::UInt64(dependency_drops_);
  event["keyframe_request_drops"] = Json::UInt64(keyframe_request_drops_);
  event["enqueue_rejections"] = Json::UInt64(enqueue_rejections_);
  event["last_frame_age_ms"] = last_frame_age_ms_;
  event["in_flight_limit_ms"] = sender_->GetMaxInFlightMediaDuration().count() / 1000.0;
  event["last_enqueue_result"] = last_enqueue_result_;
  if (audio_ || system_audio_) {
    event["audio_sent"] = Json::UInt64(audio_sent_);
    event["audio_dropped"] = Json::UInt64(audio_dropped_);
    event["audio_in_flight"] = Json::UInt64(audio_sender_->GetInFlightFrameCount());
    event["audio_round_trip_ms"] = audio_sender_->GetCurrentRoundTripTime().count() / 1000.0;
    if (system_audio_) {
      event["audio_queue_replaced"] = Json::UInt64(system_audio_->Replaced());
      event["audio_discontinuities"] = Json::UInt64(system_audio_->Discontinuities());
    }
  }
  if (clip_.desktop) event["capture_queue_replaced"] = Json::UInt64(clip_.desktop->Replaced());
  Emit(event);
}

void Probe::AudioTick() {
  try {
    if (system_audio_) {
      AudioPacket packet;
      if (!system_audio_->Next(packet)) { runner_.PostTaskWithDelay([this] { AudioTick(); }, 5ms); return; }
      EncodedFrame frame;
      frame.dependency = EncodedFrame::Dependency::kKeyFrame;
      frame.frame_id = audio_sender_->GetNextFrameId(); frame.referenced_frame_id = frame.frame_id;
      frame.rtp_timestamp = RtpTimeTicks() + RtpTimeDelta::FromTicks(48000 + packet.samples);
      frame.reference_time = packet.reference; frame.data = packet.data;
      if (Clock::now() - packet.reference > 100ms) ++audio_dropped_;
      else if (audio_sender_->EnqueueFrame(frame) == Sender::OK) ++audio_sent_; else ++audio_dropped_;
      runner_.PostTask([this] { AudioTick(); }); return;
    }
    if (audio_index_ >= audio_->packets.size()) return;
    auto offset = std::chrono::milliseconds(audio_index_ * 20);
    auto reference = audio_origin_ + offset, now = Clock::now();
    if (reference > now) { runner_.PostTaskWithDelay([this] { AudioTick(); }, reference - now); return; }
    if (now - reference > 100ms) ++audio_dropped_;
    else {
      EncodedFrame frame;
      frame.dependency = EncodedFrame::Dependency::kKeyFrame;
      frame.frame_id = audio_sender_->GetNextFrameId(); frame.referenced_frame_id = frame.frame_id;
      frame.rtp_timestamp = RtpTimeTicks() + RtpTimeDelta::FromTicks(48000 + audio_index_ * 960);
      frame.reference_time = reference; frame.data = audio_->packets[audio_index_];
      if (audio_sender_->EnqueueFrame(frame) == Sender::OK) ++audio_sent_; else ++audio_dropped_;
    }
    ++audio_index_; runner_.PostTask([this] { AudioTick(); });
  } catch (const std::exception& error) { Fail(error.what()); }
}

void Probe::Tick() {
  try {
    if (!loaded_) {
      if (!clip_.Next()) {
        if (clip_.desktop && !clip_.desktop->Finished()) {
          if (Clock::now() - waiting_since_ > 10s) { Fail("Desktop capture produced no frame for ten seconds"); return; }
          runner_.PostTaskWithDelay([this] { Tick(); }, 5ms); return;
        }
        Stats("finished"); runner_.PostTaskWithDelay([this] { Stop(); }, 500ms); return;
      }
      waiting_since_ = Clock::now();
      loaded_ = true;
    }
    auto reference = clip_.desktop ? clip_.desktop->FrameTime() : origin_ + clip_.Offset();
    auto now = Clock::now();
    if (reference > now) { runner_.PostTaskWithDelay([this] { Tick(); }, reference - now); return; }
    bool keyframe = !(clip_.data.front() & 1);
    if (!clip_.desktop && !keyframe) { Fail("Pattern requires all-keyframe VP8 (-g 1)"); return; }
    // A skipped dependency invalidates the chain. Recover at the next
    // half-second keyframe rather than sending corrupt or obsolete frames.
    if (have_previous_pts_ && clip_.pts != previous_pts_ + 1) references_valid_ = false;
    previous_pts_ = clip_.pts; have_previous_pts_ = true;
    last_frame_age_ms_ = (now - reference).count() / 1000.0;
    if (now - reference > 100ms || (!keyframe && (!references_valid_ || sender_->NeedsKeyFrame()))) {
      if (now - reference > 100ms) ++late_drops_;
      else if (!references_valid_) ++dependency_drops_;
      else ++keyframe_request_drops_;
      ++dropped_; references_valid_ = false;
    }
    else {
      EncodedFrame frame;
      frame.dependency = keyframe ? EncodedFrame::Dependency::kKeyFrame : EncodedFrame::Dependency::kDependent;
      frame.frame_id = sender_->GetNextFrameId();
      frame.referenced_frame_id = keyframe ? frame.frame_id : previous_frame_;
      frame.rtp_timestamp = RtpTimeTicks() + RtpTimeDelta::FromDuration(clip_.Offset() + 1s, sender_->config().rtp_timebase);
      frame.reference_time = reference; frame.capture_begin_time = reference; frame.capture_end_time = reference;
      frame.data = clip_.data;
      auto enqueue_result = sender_->EnqueueFrame(frame);
      last_enqueue_result_ = int(enqueue_result);
      if (enqueue_result == Sender::OK) {
        ++sent_; previous_frame_ = frame.frame_id; references_valid_ = true;
      } else { ++dropped_; ++enqueue_rejections_; references_valid_ = false; }
    }
    if (++frames_ % 30 == 0) Stats("stats");
    loaded_ = false; runner_.PostTask([this] { Tick(); });
  } catch (const std::exception& error) { Fail(error.what()); }
}
}  // namespace cast_to_tv
