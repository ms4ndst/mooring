Phase 6 (calls): research spike, 2026-11-19
==========================================

Outcome: calls are NOT pursued for now. Call control works; call audio does not on the
test laptop out of the box. KDE Connect already shows incoming-call notifications, which
covers the main need. This file records what was learned so the work can resume later.

Setup tested
------------
Fedora 44, PipeWire 1.6.11, WirePlumber 0.5.19, BlueZ 5.88, Intel Bluetooth 8087:0037
(firmware ibt-0093-0291), Android 15 phone paired and trusted at the Bluetooth level.
No oFono; no WirePlumber overrides.

What works
----------
- The laptop can act as HFP Hands-Free unit (HF): the adapter advertises "Handsfree",
  registered by PipeWire's native bluez5 backend (role hfp_hf, on by default).
- The phone offers Handsfree Audio Gateway, Headset AG, A2DP source, PBAP and MAP.
- `bluetoothctl connect <phone>` gives a PipeWire card in profile `audio-gateway`.
- Call control: PipeWire runs `org.pipewire.Telephony` on the session bus with an
  oFono-compatible API. Per phone: /org/pipewire/Telephony/agN with
    org.pipewire.Telephony.AudioGateway1  Dial(s), HangupAll(), SwapCalls(), HoldAndAnswer(),
        ReleaseAndAnswer(), ReleaseAndSwap(), CreateMultiparty(), SendTones(s),
        SpeakerVolume/MicrophoneVolume (y, rw)
    org.pipewire.Telephony.AudioGatewayTransport1  State (s), Codec (y), RejectSCO (b, rw),
        Activate()
    org.ofono.VoiceCallManager  Dial(ss), GetCalls(), CallAdded/CallRemoved, ...
    org.ofono.CallVolume
  Observed for real calls: CallAdded (alerting) -> active -> disconnected -> CallRemoved.
- KDE Connect telephony plugin: signal callReceived(event, number, contactName) with
  event ringing / talking / missedCall, plus a "mute ringer" action. No answer, hangup or
  audio; those must come from HFP.

What doesn't work
-----------------
Three test calls (outgoing to voicemail), audio heard nowhere (neither laptop nor phone):
- Call 1: transport never left `idle`; kernel logged "corrupted SCO packet" at hang-up.
- Call 3: transport went `active` with Codec 2 (mSBC, wideband) for 30 s, but PipeWire
  created NO bluez_input/bluez_output nodes for it, so the audio had nowhere to go.
  One "corrupted SCO packet" at hang-up.
- RejectSCO was already false, so PipeWire wasn't refusing SCO.
- The kernel had logged 13 "corrupted SCO packet" errors that boot before the test, so
  SCO trouble on this adapter isn't specific to the phone.

Not tried (next steps if this is picked up again)
------------------------------------------------
1. Narrowband: disable mSBC so SCO uses CVSD (the most robust on Intel chips), e.g. a
   ~/.config/wireplumber/wireplumber.conf.d/ drop-in setting bluez5.codecs / enable-msbc
   for the hands-free role, then restart pipewire/wireplumber and repeat the test call.
2. Debug logs: PIPEWIRE_DEBUG / WirePlumber log level for spa.bluez5 during a call, to see
   why no nodes are created for the audio-gateway profile's active transport.
3. Calling AudioGatewayTransport1.Activate() before/at call start (couldn't be tested:
   the transport was already active in the run where it would have triggered).
4. Different adapter (a USB dongle with known-good mSBC) to rule the Intel chip in or out.

If audio stays broken, a "partial" feature is still possible without audio: show an
incoming-call card in Mooring (KDE Connect's callReceived for name/number, PipeWire
Telephony for state) with Answer / Decline / Hang up buttons, audio staying on the phone.
That needs the phone connected for Bluetooth audio, which also routes media audio to the
laptop, a trade-off to weigh.
