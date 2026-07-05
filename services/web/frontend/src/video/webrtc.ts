export async function initVideo(container: HTMLElement, whepUrl: string): Promise<void> {
  const video = document.createElement("video");
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;
  // no controls attribute — browser controls hidden intentionally
  container.appendChild(video);

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  pc.ontrack = (evt) => {
    if (evt.track.kind === "video" && !video.srcObject) {
      video.srcObject = evt.streams[0];
    }
  };

  pc.addTransceiver("video", { direction: "recvonly" });
  pc.addTransceiver("audio", { direction: "recvonly" });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  // Wait for ICE gathering (acceptable on LAN; 5 s timeout as fallback)
  await Promise.race([
    new Promise<void>((resolve) => {
      if (pc.iceGatheringState === "complete") { resolve(); return; }
      const check = () => {
        if (pc.iceGatheringState === "complete") {
          pc.removeEventListener("icegatheringstatechange", check);
          resolve();
        }
      };
      pc.addEventListener("icegatheringstatechange", check);
    }),
    new Promise<void>((resolve) => setTimeout(resolve, 5000)),
  ]);

  const resp = await fetch(whepUrl, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription!.sdp,
  });

  if (!resp.ok) {
    throw new Error(`WHEP ${resp.status}: ${await resp.text()}`);
  }

  const answerSdp = await resp.text();
  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  console.log("[Video] WHEP session established");
}
