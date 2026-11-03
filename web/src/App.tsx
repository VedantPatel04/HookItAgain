import { useEffect, useState } from "react";
import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);

const apiUrl = import.meta.env.VITE_API_URL;

type Project = { id: string; name: string; created_at: string };

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
  const data = (await res.json()) as T;
  if (!res.ok) {
    throw new Error(`${res.status}`);
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
  const [events, setEvents] = useState<EventListItem[]>([]);
  const [eventDetail, setEventDetail] = useState<EventDetail | null>(null);
  const [deliveries, setDeliveries] = useState<DeliveryDetail[]>([]);

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
        .catch(() => setError("Could not load projects."));
      return;
    }
    if (route.page === "events") {
      apiGet<{ items: EventListItem[] }>(`/api/projects/${route.projectId}/events`, token)
        .then((body) => {
          setError(null);
          setEvents(body.items);
        })
        .catch(() => setError("Could not load events."));
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
      .catch(() => setError("Could not load event."));
  }, [token, route]);

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
        <h1>Events</h1>
        {error ? <p>{error}</p> : null}
        {events.length === 0 ? <p>No events.</p> : null}
        <ul>
          {events.map((event) => (
            <li key={event.id}>
              <a href={`#/projects/${route.projectId}/events/${event.id}`}>
                {event.accepted_at} {event.event_type ?? "no type"} {event.status}
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
