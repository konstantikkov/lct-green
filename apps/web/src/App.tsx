import { useEffect } from "react";
import { BottomSheet } from "./components/BottomSheet";
import { DropZone } from "./components/DropZone";
import { Inspector } from "./components/Inspector";
import { StatusBar } from "./components/StatusBar";
import { TopBar } from "./components/TopBar";
import { ConstraintsPanel } from "./components/panels/ConstraintsPanel";
import { GeneratePanel } from "./components/panels/GeneratePanel";
import { IterationsPanel } from "./components/panels/IterationsPanel";
import { LayersPanel } from "./components/panels/LayersPanel";
import { loadCapabilities, loadProjects, loadStaticData } from "./state/actions";
import { useApp } from "./state/store";
import { ViewerCanvas } from "./viewer/ViewerCanvas";

export default function App() {
  const doc = useApp((s) => s.document);

  useEffect(() => {
    void loadCapabilities();
    void loadStaticData();
    void loadProjects();
  }, []);

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        {doc && (
          <aside className="hidden w-[280px] shrink-0 flex-col overflow-y-auto border-r border-ink-600 bg-ink-900 md:flex">
            <LayersPanel />
            <ConstraintsPanel />
            <GeneratePanel />
            <IterationsPanel />
          </aside>
        )}
        <main
          className={`min-w-0 flex-1 bg-ink-950 ${
            // On mobile the fixed bottom sheet covers the bottom of the screen;
            // pad the canvas so its visible area ends at the sheet's peek edge.
            doc ? "pb-[calc(56px_+_env(safe-area-inset-bottom))] md:pb-0" : ""
          }`}
        >
          {doc ? <ViewerCanvas /> : <DropZone />}
        </main>
        <Inspector />
      </div>
      <StatusBar />
      {doc && <BottomSheet />}
    </div>
  );
}
