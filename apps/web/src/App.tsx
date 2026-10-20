import { useEffect } from "react";
import { AdminDashboard } from "./components/AdminDashboard";
import { BottomSheet } from "./components/BottomSheet";
import { Inspector } from "./components/Inspector";
import { StatusBar } from "./components/StatusBar";
import { TopBar } from "./components/TopBar";
import { Wizard } from "./components/Wizard";
import { WorkspacePanel, WorkspaceTabs } from "./components/Workspace";
import { AuditView } from "./components/views/AuditView";
import { QualityView } from "./components/views/QualityView";
import { RegulationsView } from "./components/views/RegulationsView";
import { RunsView } from "./components/views/RunsView";
import { loadCapabilities, loadProjects, loadStaticData } from "./state/actions";
import { useApp } from "./state/store";
import { ViewerCanvas } from "./viewer/ViewerCanvas";

/**
 * PLAN — the default workflow: workspace tab strip (one section at a time)
 * + compact left panel + canvas (hero) + inspector.
 */
function PlanView() {
  const doc = useApp((s) => s.document);
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <WorkspaceTabs />
      <div className="flex min-h-0 flex-1">
        <WorkspacePanel />
        <main
          className={`flex min-h-0 min-w-0 flex-1 flex-col bg-ink-950 ${
            // On mobile the fixed bottom sheet covers the bottom of the screen;
            // pad the canvas so its visible area ends at the sheet's peek edge.
            doc ? "pb-[calc(56px_+_env(safe-area-inset-bottom))] md:pb-0" : ""
          }`}
        >
          <div className="min-h-0 flex-1"><ViewerCanvas /></div>
          <Inspector />
        </main>
      </div>
    </div>
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

  // Regulations & Quality are global views — reachable from the dashboard
  // (and via /regulations, /quality deep links) without an open project.
  if (!doc && (nav === "regulations" || nav === "quality")) {
    return (
      <div className="flex h-full flex-col">
        <TopBar />
        <div className="flex min-h-0 flex-1">
          {nav === "regulations" ? <RegulationsView /> : <QualityView />}
        </div>
      </div>
    );
  }

  // Stage "empty": no project open — the admin dashboard is the start screen.
  if (!doc) {
    return (
      <div className="flex h-full flex-col">
        <main className="min-h-0 flex-1">
          <AdminDashboard />
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
        {nav === "audit" && <AuditView />}
      </div>
      {nav === "plan" && <StatusBar />}
      {nav === "plan" && <BottomSheet />}
      <Wizard />
    </div>
  );
}
