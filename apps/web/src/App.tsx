import { useEffect } from "react";
import { DropZone } from "./components/DropZone";
import { Inspector } from "./components/Inspector";
import { StatusBar } from "./components/StatusBar";
import { TopBar } from "./components/TopBar";
import { ConstraintsPanel } from "./components/panels/ConstraintsPanel";
import { GeneratePanel } from "./components/panels/GeneratePanel";
import { IterationsPanel } from "./components/panels/IterationsPanel";
import { LayersPanel } from "./components/panels/LayersPanel";
import { loadCapabilities, loadStaticData } from "./state/actions";
import { useApp } from "./state/store";
import { ViewerCanvas } from "./viewer/ViewerCanvas";

export default function App() {
  const doc = useApp((s) => s.document);

  useEffect(() => {
    void loadCapabilities();
    void loadStaticData();
  }, []);

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        {doc && (
          <aside className="flex w-[280px] shrink-0 flex-col overflow-y-auto border-r border-ink-600 bg-ink-900">
            <LayersPanel />
            <ConstraintsPanel />
            <GeneratePanel />
            <IterationsPanel />
          </aside>
        )}
        <main className="min-w-0 flex-1 bg-ink-950">
          {doc ? <ViewerCanvas /> : <DropZone />}
        </main>
        <Inspector />
      </div>
      <StatusBar />
    </div>
  );
}
