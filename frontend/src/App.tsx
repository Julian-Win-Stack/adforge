import { useState } from "react";
import { JobView } from "./JobView";
import { StartForm } from "./StartForm";

export function App() {
  const [jobId, setJobId] = useState<string | null>(null);

  return (
    <main style={{ maxWidth: 720, margin: "2rem auto", fontFamily: "system-ui, sans-serif" }}>
      <h1>AdForge</h1>
      {jobId === null ? (
        <StartForm onStarted={setJobId} />
      ) : (
        <>
          <JobView jobId={jobId} />
          <button onClick={() => setJobId(null)}>Start another</button>
        </>
      )}
    </main>
  );
}
