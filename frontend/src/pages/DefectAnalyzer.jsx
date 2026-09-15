import { useState } from "react";
import { api } from "../services/api";
import Panel from "../components/Panel.jsx";
import WaferMapViewer from "../components/WaferMapViewer.jsx";
import { ErrorState } from "../components/LoadingState.jsx";

export default function DefectAnalyzer() {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  function handleFile(selected) {
    setFile(selected);
    setResult(null);
    setError(null);
    setPreviewUrl(selected ? URL.createObjectURL(selected) : null);
  }

  async function analyze() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await api.post("/defects/analyze", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      if (!response.data.success) throw new Error(response.data.error?.message || "Analysis failed");
      setResult(response.data.data);
    } catch (err) {
      setError({ message: err.response?.data?.error?.message || err.message });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink-primary">Defect Analyzer</h1>
        <p className="text-sm text-ink-secondary">
          Upload a wafer map image for pattern classification. This is <span className="text-ink-primary font-medium">wafer map pattern
          analysis</span>, not microscope defect-image analysis.
        </p>
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Panel title="Upload Image">
          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]);
            }}
            className="border-2 border-dashed border-surface-border rounded-lg p-8 text-center hover:border-brand/40 transition-colors"
          >
            {previewUrl ? (
              <img src={previewUrl} alt="preview" className="max-h-48 mx-auto rounded" />
            ) : (
              <p className="text-sm text-ink-muted">Drag & drop an image here, or click below to choose one.</p>
            )}
            <input
              type="file"
              accept="image/png,image/jpeg,image/bmp,image/tiff"
              onChange={(e) => handleFile(e.target.files?.[0] || null)}
              className="mt-4 mx-auto block text-xs text-ink-secondary file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:bg-brand/15 file:text-brand file:text-xs"
            />
          </div>
          <button
            onClick={analyze}
            disabled={!file || loading}
            className="mt-4 w-full py-2 rounded-md bg-brand text-surface font-medium text-sm hover:bg-brand/90 disabled:opacity-50"
          >
            {loading ? "Analyzing…" : "Analyze"}
          </button>
          {error && <div className="mt-3"><ErrorState error={error} /></div>}
        </Panel>

        <Panel title="AI Result">
          {!result ? (
            <div className="text-sm text-ink-muted py-8 text-center">Upload and analyze an image to see results.</div>
          ) : (
            <div className="space-y-4">
              <div className="text-xs text-status-warning">{result.note}</div>
              <div>
                <div className="text-2xl font-semibold text-ink-primary">{result.predicted_pattern}</div>
                <div className="text-sm text-ink-muted">Confidence: {Math.round(result.confidence * 100)}%</div>
              </div>
              <div>
                <div className="label-caps mb-1.5">Alternatives</div>
                {result.alternatives.map((alt) => (
                  <div key={alt.pattern} className="flex justify-between text-xs py-0.5">
                    <span className="text-ink-secondary">{alt.pattern}</span>
                    <span className="text-ink-muted tabular-nums">{Math.round(alt.probability * 100)}%</span>
                  </div>
                ))}
              </div>
              <div>
                <div className="label-caps mb-1.5">Preprocessed Wafer Map</div>
                <WaferMapViewer dieGrid={result.die_grid} gradCam={result.grad_cam} height={220} />
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
