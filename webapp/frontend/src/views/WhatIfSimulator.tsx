import { useCallback, useEffect, useRef, useState } from "react";
import "../App.css";
import {
  fetchCurrentState, runSimulation,
  type ReadingsSnapshot, type SimulationResult,
} from "../api";
import { Button, Fine, Skeleton } from "../components/Type";
import { ScenarioForm } from "../components/ScenarioForm";
import { ResultsPanel } from "../components/ResultsPanel";

const DEBOUNCE_MS = 300;

/** Matches the dashboards, so the whole page tracks the twin at one cadence. */
const REFRESH_MS = 15000;

/** Fields the twin owns. Compared to decide whether it has actually moved. */
function twinChanged(a: ReadingsSnapshot, b: ReadingsSnapshot): boolean {
  return (Object.keys(a) as (keyof ReadingsSnapshot)[]).some((key) => {
    const x = a[key];
    const y = b[key];
    if (typeof x === "number" && typeof y === "number") {
      // Float noise in the last places is not the twin "moving".
      return Math.abs(x - y) > 1e-6;
    }
    return x !== y;
  });
}

export function WhatIfSimulator() {
  // What the model is being run against - the twin's values until edited.
  const [snapshot, setSnapshot] = useState<ReadingsSnapshot | null>(null);
  // The twin's latest, kept separately so edits are never overwritten.
  const [twin, setTwin] = useState<ReadingsSnapshot | null>(null);
  // The twin reading the current edits branched from, for detecting drift.
  const [branchedFrom, setBranchedFrom] = useState<ReadingsSnapshot | null>(null);
  const [edited, setEdited] = useState(false);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /*
   * Poll the twin, but only adopt its values while the scenario is untouched.
   *
   * Overwriting an edited form would discard the user's scenario mid-thought,
   * which is worse than showing stale inputs. Once edited we keep their values
   * and surface the drift instead, so the choice to resync stays theirs.
   */
  useEffect(() => {
    let cancelled = false;

    const poll = () =>
      fetchCurrentState()
        .then((latest) => {
          if (cancelled) return;
          setTwin(latest);
          setError(null);
          setSnapshot((current) => (current === null ? latest : current));
          setBranchedFrom((current) => (current === null ? latest : current));
        })
        .catch((err) => { if (!cancelled) setError(String(err)); });

    poll();
    const id = setInterval(poll, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Adopt twin updates while the form is untouched.
  useEffect(() => {
    if (!edited && twin) {
      setSnapshot(twin);
      setBranchedFrom(twin);
    }
  }, [twin, edited]);

  // Re-run whenever the inputs settle, whether the change came from the user
  // or from the twin.
  useEffect(() => {
    if (!snapshot) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setLoading(true);
      runSimulation(snapshot)
        .then((res) => { setResult(res); setError(null); })
        .catch((err) => setError(String(err)))
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [snapshot]);

  const edit = useCallback((patch: Partial<ReadingsSnapshot>) => {
    setEdited(true);
    setSnapshot((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const resync = useCallback(() => {
    if (!twin) return;
    setSnapshot(twin);
    setBranchedFrom(twin);
    setEdited(false);
  }, [twin]);

  if (!snapshot) {
    if (error) return <Fine>Failed to load: {error}</Fine>;
    return (
      <div className="app-grid" aria-busy="true" aria-live="polite">
        <span className="sr-only">Reading the twin…</span>
        <div className="panel">
          <Skeleton className="h-3 w-36 mb-6" />
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="mb-5">
              <Skeleton className="h-2 w-28" />
              <Skeleton className="h-8 w-full mt-2" />
            </div>
          ))}
        </div>
        <div className="panel">
          <Skeleton className="h-3 w-44 mb-6" />
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-14 w-full mt-6" />
          <div className="grid grid-cols-2 gap-4 mt-6">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12 w-full" />)}
          </div>
        </div>
      </div>
    );
  }

  const drifted = edited && twin && branchedFrom && twinChanged(twin, branchedFrom);

  return (
    <>
      {error && (
        <div className="border border-ink/15 border-l-[3px] border-l-crit bg-paper px-4 py-3 text-[0.8125rem] text-ink-soft mb-4">
          {error}
        </div>
      )}

      {drifted && (
        <div className="border border-ink/15 border-l-[3px] border-l-warn bg-paper px-4 py-3 mb-4 flex flex-wrap items-center justify-between gap-3">
          <Fine className="max-w-[60ch]">
            <strong className="text-ink">The twin has moved since you started.</strong>{" "}
            Your scenario is being projected from the values you set, not the
            current readings.
          </Fine>
          <Button variant="ghost" onClick={resync}>Use current readings</Button>
        </div>
      )}

      {!edited && (
        <Fine className="mb-4">
          Tracking the twin live. Change any parameter to branch off and hold it.
        </Fine>
      )}

      <main className="app-grid">
        <ScenarioForm
          snapshot={snapshot}
          onFieldChange={(key, value) => edit({ [key]: value } as Partial<ReadingsSnapshot>)}
          onStageChange={(stage) => edit({ growth_stage: stage })}
          onPreset={(values) => edit(values)}
          onReset={resync}
        />
        <ResultsPanel result={result} loading={loading} />
      </main>
    </>
  );
}
