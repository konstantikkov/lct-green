import { useCallback, useRef, useState } from "react";
import { uploadFile } from "../state/actions";
import { useApp } from "../state/store";
import { Btn, Spinner } from "./ui";

export function DropZone() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const uploading = useApp((s) => s.uploading);
  const uploadError = useApp((s) => s.uploadError);
  const capabilities = useApp((s) => s.capabilities);

  const onFiles = useCallback((files: FileList | null) => {
    const file = files?.[0];
    if (file) void uploadFile(file);
  }, []);

  const converterMissing = uploadError?.code === "DWG_CONVERTER_UNAVAILABLE";

  return (
    <div
      className={`flex h-full w-full items-center justify-center transition-colors ${
        dragOver ? "bg-ink-850" : "bg-ink-950"
      }`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        onFiles(e.dataTransfer.files);
      }}
    >
      <div
        className={`mx-4 flex w-full max-w-lg flex-col items-center gap-4 border border-dashed px-8 py-12 ${
          dragOver ? "border-emerald-600" : "border-ink-500"
        }`}
      >
        <div className="font-mono text-2xs uppercase tracking-widest text-ink-500">
          GreenPlan · site plan intake
        </div>
        <div className="text-center text-lg text-ink-100">Drop DWG / DXF</div>
        <div className="text-center text-xs text-ink-400">
          The plan is parsed, layers are classified and normalized to meters.
        </div>

        {uploading ? (
          <div className="flex items-center gap-2 text-xs text-ink-300">
            <Spinner /> Uploading and parsing…
          </div>
        ) : (
          <Btn variant="primary" onClick={() => inputRef.current?.click()}>
            Choose file…
          </Btn>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".dxf,.dwg"
          className="hidden"
          onChange={(e) => {
            onFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {uploadError && !converterMissing && (
          <div className="w-full border border-red-800 bg-red-950/50 p-2 text-2xs text-red-300">
            <span className="font-semibold">Upload failed{uploadError.code ? ` (${uploadError.code})` : ""}:</span>{" "}
            {uploadError.message}
          </div>
        )}

        {converterMissing && (
          <div className="w-full border border-amber-700 bg-amber-950/40 p-2.5 text-2xs leading-relaxed text-amber-200">
            <div className="mb-1 font-semibold uppercase tracking-wide">DWG converter unavailable</div>
            <p className="text-amber-200/90">
              This file is a binary DWG. The backend converts DWG → DXF with an external tool,
              and none was found. Install one of:
            </p>
            <ul className="my-1 list-inside list-disc text-amber-200/90">
              <li><span className="font-mono">ODA File Converter</span> — set <span className="font-mono">ODA_CONVERTER_PATH</span></li>
              <li><span className="font-mono">LibreDWG dwg2dxf</span> — set <span className="font-mono">DWG2DXF_PATH</span> (or have it on PATH)</li>
            </ul>
            <p className="text-amber-200/90">
              Then restart the API. Alternatively, export the drawing as DXF and upload that instead.
            </p>
            <p className="mt-1 text-amber-400/80">Backend message: {uploadError?.message}</p>
          </div>
        )}

        {capabilities && !capabilities.dwgConverter.available && !uploadError && (
          <div className="text-2xs text-ink-500">
            Note: DWG converter not detected on the backend — DXF only.
          </div>
        )}
      </div>
    </div>
  );
}
