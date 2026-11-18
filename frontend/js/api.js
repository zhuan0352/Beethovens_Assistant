async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error("Can't reach the Beethoven Assistant server. Is it still running?");
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return response;
}

export async function createProject(wavBlob, beatsPerBar = 4) {
  const form = new FormData();
  form.append("audio", wavBlob, "take.wav");
  form.append("beats_per_bar", String(beatsPerBar));
  return (await request("/api/projects", { method: "POST", body: form })).json();
}

export async function getProject(id) {
  return (await request(`/api/projects/${id}`)).json();
}

export async function rearrange(id, settings) {
  return (
    await request(`/api/projects/${id}/rearrange`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    })
  ).json();
}

export async function fetchAudio(path) {
  return (await request(path)).arrayBuffer();
}

export const midiUrl = (id, style) => `/api/projects/${id}/midi/${style}`;
