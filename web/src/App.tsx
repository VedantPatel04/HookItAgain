import { useEffect, useState } from "react";
import { createClient } from "@supabase/supabase-js";

const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);

const apiUrl = import.meta.env.VITE_API_URL;

export default function App() {
  const [body, setBody] = useState("not signed in");

  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) return;
      const res = await fetch(`${apiUrl}/api/projects`, {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      setBody(`${res.status} ${await res.text()}`);
    });
  }, []);

  return (
    <main>
      <button
        type="button"
        onClick={() => supabase.auth.signInWithOAuth({ provider: "github" })}
      >
        Sign in with GitHub
      </button>
      <pre>{body}</pre>
    </main>
  );
}