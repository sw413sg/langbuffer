"""Adaptadores de metadata: sin HTTP, extracción ni reproducción al importarlos."""
from collections.abc import Mapping
from .contracts import (CandidatePlan, Kind, Platform, Quality, SourceError,
                        StateEvidence, Transport)
from .format_policy import number, select_candidate


def validate_identity(source, metadata):
    if not isinstance(metadata, Mapping):
        raise SourceError("invalid_source_metadata")
    if metadata.get("_type") not in (None, "video") or "entries" in metadata:
        raise SourceError("single_content_required")
    if str(metadata.get("id", "")) != source.content_id:
        raise SourceError("content_identity_changed")
    if metadata.get("availability") not in (None, "public", "unlisted"):
        raise SourceError("source_access_restricted")
    if metadata.get("has_drm") is True:
        raise SourceError("drm_unsupported")


def make_plan(source, kind, metadata, quality):
    selected, catalog = select_candidate(source, kind, metadata, quality)
    duration = number(metadata.get("duration")) or None
    if kind is Kind.VIDEO and duration is None:
        raise SourceError("finite_duration_unverified")
    requirements = [
        "bounded_network_worker",
        "verify_av_decode_and_pts",
        "verify_single_audible_output",
        "verify_realtime_asr_and_delayed_playback",
        "verify_cancel_generation_and_cleanup",
    ]
    if selected.transport is Transport.HLS:
        requirements += ["validate_hls_features_and_limits"]
        requirements += (["verify_live_manifest_progress"] if kind is Kind.LIVE
                         else ["verify_finite_hls"])
    else:
        requirements += ["verify_http_range_and_finite_resource",
                         "implement_mp4_asr_transport"]
    if not selected.codecs_known:
        requirements.append("probe_unknown_codecs")
    if not selected.quality.height:
        requirements.append("probe_unknown_dimensions")
    if source.platform is Platform.FACEBOOK:
        requirements.append("revalidate_facebook_state_for_generation")
    return CandidatePlan(source, kind, selected, catalog, duration, tuple(requirements))


class YouTubeLiveAdapter:
    @staticmethod
    def validate_state(metadata):
        state = metadata.get('live_status')
        codes = {'is_upcoming': 'source_scheduled', 'post_live': 'youtube_live_ended',
                 'was_live': 'youtube_live_ended', 'not_live': 'youtube_vod_out_of_scope'}
        if isinstance(state, str) and state in codes:
            raise SourceError(codes[state])
        if state != 'is_live':
            raise SourceError('live_state_unknown')
        if metadata.get('is_live') is False:
            raise SourceError('live_state_conflict')

    def plan(self, source, metadata, quality=Quality()):
        if source.platform is not Platform.YOUTUBE:
            raise SourceError("platform_mismatch")
        validate_identity(source, metadata)
        self.validate_state(metadata)
        return make_plan(source, Kind.LIVE, metadata, quality)


class FacebookAdapter:
    def plan(self, source, metadata, quality=Quality(), *, evidence=None, request_id=None):
        if source.platform is not Platform.FACEBOOK:
            raise SourceError("platform_mismatch")
        validate_identity(source, metadata)
        if not isinstance(evidence, StateEvidence):
            raise SourceError("facebook_state_unverified")
        if evidence.content_id != source.content_id:
            raise SourceError("content_identity_changed")
        if evidence.request_id != request_id:
            raise SourceError("stale_state_evidence")
        allowed = {
            Kind.LIVE: {"hls_growing", "dash_dynamic", "authoritative_state"},
            Kind.VIDEO: {"finite_vod", "authoritative_state"},
        }
        if evidence.basis not in allowed.get(evidence.kind, set()):
            raise SourceError("facebook_state_unverified")
        # Nunca inferir VIDEO de is_live ausente ni LIVE de /watch/live/.
        if metadata.get("is_live") is True and evidence.kind is not Kind.LIVE:
            raise SourceError("live_state_conflict")
        return make_plan(source, evidence.kind, metadata, quality)
