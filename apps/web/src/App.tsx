import { useEffect } from "react";
import { BottomSheet } from "./components/BottomSheet";
import { EmptyState } from "./components/EmptyState";
import { Inspector } from "./components/Inspector";
import { PlanSidebar } from "./components/PlanSidebar";
import { StatusBar } from "./components/StatusBar";
import { TopBar } from "./components/TopBar";
import { QualityView } from "./components/views/QualityView";
import { RegulationsView } from "./components/views/RegulationsView";
import { RunsView } from "./components/views/RunsView";
import { loadCapabilities, loadProjects, loadStaticData } from "./state/actions";
import { useApp } from "./state/store";
import { ViewerCanvas } from "./viewer/ViewerCanvas";

/** PLAN — the default workflow: sidebar + canvas (hero) + inspector. */
function PlanView() {
  const doc = useApp((s) => s.document);
  return (
    <>
      <PlanSidebar />
      <main
        className={`min-w-0 flex-1 bg-ink-950 ${
          // On mobile the fixed bottom sheet covers the bottom of the screen;
          // pad the canvas so its visible area ends at the sheet's peek edge.
          doc ? "pb-[calc(56px_+_env(safe-area-inset-bottom))] md:pb-0" : ""
        }`}
      >
        <ViewerCanvas />
      </main>
      <Inspector />
    </>
  );
}

export default function App() {
  const doc = useApp((s) => s.document);
  const nav = useApp((s) => s.nav);

  useEffect(() => {
    void loadCapabilities();
    void loadStaticData();
    void loadProjects();
  }, []);

  // Stage "empty": near-blank landing — no panels, no status bar clutter.
  if (!doc) {
    return (
      <div className="flex h-full flex-col">
        <main className="min-h-0 flex-1">
          <EmptyState />
        </main>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        {nav === "plan" && <PlanView />}
        {nav === "regulations" && <RegulationsView />}
        {nav === "runs" && <RunsView />}
        {nav === "quality" && <QualityView />}
      </div>
      {nav === "plan" && <StatusBar />}
      {nav === "plan" && <BottomSheet />}
    </div>
  );
}
