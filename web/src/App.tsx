import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);

const apiUrl = import.meta.env.VITE_API_URL;

type Project = { id: string; name: string; created_at: string };

type Destination = {
  id: string;
  name: string;
  type: string;
  url: string | null;
  created_at: string;
  signing_secret?: string;
};

type Source = {
  id: string;
  name: string;
  provider: string;
  destination_id: string;
  ingest_url: string;
  created_at: string;
  secret?: string;
};

type EventListItem = {
  id: string;
  source_id: string;
  provider_delivery_id: string;
  event_type: string | null;
  status: string;
  correlation_id: string;
  accepted_at: string;
};

type DeliverySummary = {
  id: string;
  destination_id: string;
  status: string;
  attempt_count: number;
  replayed_from_delivery_id: string | null;
  suite_run_id: string | null;
};

type EventDetail = EventListItem & {
  project_id: string;
  byte_size: number;
  body: string | null;
  content_type: string | null;
  deliveries: DeliverySummary[];
};

type Attempt = {
  id: string;
  attempt_number: number;
  http_status: number | null;
  duration_ms: number;
  error_category: string | null;
  response_excerpt: string | null;
  correlation_id: string;
  completed_at: string;
};

type DeliveryDetail = {
  id: string;
  event_id: string;
  destination_id: string;
  status: string;
  attempts: Attempt[];
};

type Route =
  | { page: "projects" }
  | { page: "events"; projectId: string }
  | { page: "event"; projectId: string; eventId: string };

type ApiError = { error?: { message?: string } };

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#/, "");
  const parts = raw.split("/").filter(Boolean);
  if (parts[0] === "projects" && parts[1] && parts[2] === "events" && parts[3]) {
    return { page: "event", projectId: parts[1], eventId: parts[3] };
  }
  if (parts[0] === "projects" && parts[1]) {
    return { page: "events", projectId: parts[1] };
  }
  return { page: "projects" };
}

async function apiGet<T>(path: string, token: string): Promise<T> {
  const res = await fetch(`${apiUrl}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const data = (await res.json()) as T & ApiError;
  if (!res.ok) {
    throw new Error(data.error?.message ?? `${res.status}`);
  }
  return data;
}

async function apiPost<T>(path: string, token: string, body: unknown): Promise<T> {
  const res = await fetch(`${apiUrl}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const data = (await res.json()) as T & ApiError;
  if (!res.ok) {
    throw new Error(data.error?.message ?? `${res.status}`);
  }
  return data;
}

function firstSuccess(attempts: Attempt[]): Attempt | null {
  for (const attempt of attempts) {
    if (attempt.error_category === null && attempt.http_status !== null && attempt.http_status >= 200 && attempt.http_status <= 299) {
      return attempt;
    }
  }
  return null;
}

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [route, setRoute] = useState<Route>(parseHash);
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [destinations, setDestinations] = useState<Destination[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [events, setEvents] = useState<EventListItem[]>([]);
  const [eventDetail, setEventDetail] = useState<EventDetail | null>(null);
  const [deliveries, setDeliveries] = useState<DeliveryDetail[]>([]);
  const [projectName, setProjectName] = useState("");
  const [destName, setDestName] = useState("");
  const [destUrl, setDestUrl] = useState("");
  const [sourceName, setSourceName] = useState("");
  const [sourceDestinationId, setSourceDestinationId] = useState("");
  const [onceSecret, setOnceSecret] = useState<string | null>(null);
  const [onceIngestUrl, setOnceIngestUrl] = useState<string | null>(null);
  const [onceSigningSecret, setOnceSigningSecret] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setToken(session?.access_token ?? null);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setToken(session?.access_token ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!token) return;
    if (route.page === "projects") {
      apiGet<{ items: Project[] }>("/api/projects", token)
        .then((body) => {
          setError(null);
          setProjects(body.items);
        })
        .catch((err: Error) => setError(err.message || "Could not load projects."));
      return;
    }
    if (route.page === "events") {
      Promise.all([
        apiGet<{ items: EventListItem[] }>(`/api/projects/${route.projectId}/events`, token),
        apiGet<{ items: Destination[] }>(`/api/projects/${route.projectId}/destinations`, token),
        apiGet<{ items: Source[] }>(`/api/projects/${route.projectId}/sources`, token),
      ])
        .then(([eventBody, destBody, sourceBody]) => {
          setError(null);
          setEvents(eventBody.items);
          setDestinations(destBody.items);
          setSources(sourceBody.items);
          if (destBody.items.length > 0) {
            setSourceDestinationId(destBody.items[0].id);
          }
        })
        .catch((err: Error) => setError(err.message || "Could not load project."));
      return;
    }
    apiGet<EventDetail>(`/api/events/${route.eventId}`, token)
      .then(async (detail) => {
        setError(null);
        setEventDetail(detail);
        const loaded: DeliveryDetail[] = [];
        for (const summary of detail.deliveries) {
          const delivery = await apiGet<DeliveryDetail>(`/api/deliveries/${summary.id}`, token);
          loaded.push(delivery);
        }
        setDeliveries(loaded);
      })
      .catch((err: Error) => setError(err.message || "Could not load event."));
  }, [token, route]);

  function onCreateProject(event: FormEvent) {
    event.preventDefault();
    if (!token) return;
    const name = projectName.trim();
    if (!name) return;
    apiPost<Project>("/api/projects", token, { name })
      .then((project) => {
        setError(null);
        setProjectName("");
        setProjects([...projects, project]);
      })
      .catch((err: Error) => setError(err.message));
  }

  function onCreateDestination(event: FormEvent) {
    event.preventDefault();
    if (!token || route.page === "projects") return;
    const name = destName.trim();
    const url = destUrl.trim();
    if (!name || !url) return;
    apiPost<Destination>(`/api/projects/${route.projectId}/destinations`, token, {
      name,
      type: "https",
      url,
    })
      .then((destination) => {
        setError(null);
        setDestName("");
        setDestUrl("");
        setDestinations([...destinations, destination]);
        setSourceDestinationId(destination.id);
        setOnceSigningSecret(destination.signing_secret ?? null);
        setOnceSecret(null);
        setOnceIngestUrl(null);
      })
      .catch((err: Error) => setError(err.message));
  }

  function onCreateSource(event: FormEvent) {
    event.preventDefault();
    if (!token || route.page === "projects") return;
    const name = sourceName.trim();
    if (!name || !sourceDestinationId) return;
    apiPost<Source>(`/api/projects/${route.projectId}/sources`, token, {
      name,
      provider: "generic_hmac",
      destination_id: sourceDestinationId,
    })
      .then((source) => {
        setError(null);
        setSourceName("");
        setSources([...sources, source]);
        setOnceSecret(source.secret ?? null);
        setOnceIngestUrl(source.ingest_url);
        setOnceSigningSecret(null);
      })
      .catch((err: Error) => setError(err.message));
  }

  if (!token) {
    return (
      <main className="lab">
        <p>not signed in</p>
        <button
          type="button"
          onClick={() => supabase.auth.signInWithOAuth({ provider: "github" })}
        >
          Sign in with GitHub
        </button>
      </main>
    );
  }

  if (route.page === "projects") {
    return (
      <main className="lab">
        <h1>Projects</h1>
        {error ? <p>{error}</p> : null}
        <form onSubmit={onCreateProject}>
          <label htmlFor="project-name">Name</label>
          <input
            id="project-name"
            value={projectName}
            onChange={(change) => setProjectName(change.target.value)}
          />
          <button type="submit">Create project</button>
        </form>
        {projects.length === 0 ? <p>No projects.</p> : null}
        <ul>
          {projects.map((project) => (
            <li key={project.id}>
              <a href={`#/projects/${project.id}`}>{project.name}</a>
            </li>
          ))}
        </ul>
      </main>
    );
  }

  if (route.page === "events") {
    return (
      <main className="lab">
        <p>
          <a href="#/">Projects</a>
        </p>
        <h1>Project</h1>
        {error ? <p>{error}</p> : null}

        <h2>Destination</h2>
        <form onSubmit={onCreateDestination}>
          <label htmlFor="dest-name">Name</label>
          <input
            id="dest-name"
            value={destName}
            onChange={(change) => setDestName(change.target.value)}
          />
          <label htmlFor="dest-url">HTTPS URL</label>
          <input
            id="dest-url"
            value={destUrl}
            onChange={(change) => setDestUrl(change.target.value)}
          />
          <button type="submit">Create destination</button>
        </form>
        {onceSigningSecret ? (
          <p className="once">
            destination signing_secret (once) {onceSigningSecret}
          </p>
        ) : null}
        <ul>
          {destinations.map((destination) => (
            <li key={destination.id}>
              {destination.name} {destination.url}
            </li>
          ))}
        </ul>

        <h2>Source</h2>
        <form onSubmit={onCreateSource}>
          <label htmlFor="source-name">Name</label>
          <input
            id="source-name"
            value={sourceName}
            onChange={(change) => setSourceName(change.target.value)}
          />
          <label htmlFor="source-destination">Destination</label>
          <select
            id="source-destination"
            value={sourceDestinationId}
            onChange={(change) => setSourceDestinationId(change.target.value)}
          >
            {destinations.length === 0 ? <option value="">Create a destination first</option> : null}
            {destinations.map((destination) => (
              <option key={destination.id} value={destination.id}>
                {destination.name}
              </option>
            ))}
          </select>
          <button type="submit">Create source</button>
        </form>
        {onceIngestUrl ? <p className="once">ingest_url {onceIngestUrl}</p> : null}
        {onceSecret ? <p className="once">source secret (once) {onceSecret}</p> : null}
        <ul>
          {sources.map((source) => (
            <li key={source.id}>
              {source.name} {source.ingest_url}
            </li>
          ))}
        </ul>

        <h2>Events</h2>
        {events.length === 0 ? <p>No events.</p> : null}
        <ul>
          {events.map((item) => (
            <li key={item.id}>
              <a href={`#/projects/${route.projectId}/events/${item.id}`}>
                {item.accepted_at} {item.event_type ?? "no type"} {item.status}
              </a>
            </li>
          ))}
        </ul>
      </main>
    );
  }

  const successfulAttempts: Attempt[] = [];
  for (const delivery of deliveries) {
    const attempt = firstSuccess(delivery.attempts);
    if (attempt !== null) {
      successfulAttempts.push(attempt);
    }
  }

  return (
    <main className="lab">
      <p>
        <a href="#/">Projects</a>
        {" / "}
        <a href={`#/projects/${route.projectId}`}>Events</a>
      </p>
      <h1>Event</h1>
      {error ? <p>{error}</p> : null}
      {eventDetail === null ? (
        <p>Loading.</p>
      ) : (
        <>
          <p>correlation_id {eventDetail.correlation_id}</p>
          <p>status {eventDetail.status}</p>
          <p>accepted_at {eventDetail.accepted_at}</p>
          {successfulAttempts.length > 0 ? (
            <p>
              {successfulAttempts[0].http_status} in {successfulAttempts[0].duration_ms} ms
            </p>
          ) : (
            <p>No successful attempt yet.</p>
          )}
          <h2>Body</h2>
          <pre>{eventDetail.body ?? "null"}</pre>
          <h2>Deliveries</h2>
          {deliveries.map((delivery) => (
            <section key={delivery.id}>
              <p>
                {delivery.id} {delivery.status}
              </p>
              <ul>
                {delivery.attempts.map((attempt) => (
                  <li key={attempt.id}>
                    #{attempt.attempt_number} {attempt.http_status ?? "no status"} {attempt.duration_ms} ms{" "}
                    {attempt.error_category ?? "ok"}
                    {attempt.response_excerpt ? ` ${attempt.response_excerpt}` : ""}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </main>
  );
}
