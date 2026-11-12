from __future__ import annotations

from datetime import datetime
from pathlib import Path

from academic_figure_agent.llm import BailianFigurePlanner
from academic_figure_agent.schemas import ArtifactManifest, CaptionSet, FigureRequest
from academic_figure_agent.tools import (
    execute_python_renderer,
    extract_context,
    generate_code_bundle,
    ingest_data,
    inspect_artifacts,
)
from config.settings import Settings, get_settings

from .state import FigureAgentState


class FigureNodes:
    def __init__(
        self,
        planner: BailianFigurePlanner | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.planner = planner or BailianFigurePlanner(settings=self.settings)

    def prepare(self, state: FigureAgentState) -> FigureAgentState:
        request = state["request"]
        output_dir = self._resolve_output_dir(request)
        context = extract_context(request.context_files, request.sketch_files, self.settings)
        dataset = ingest_data(request.data_files, output_dir, self.settings)
        return {"output_dir": str(output_dir), "context": context, "dataset": dataset, "revision": 0}

    def plan(self, state: FigureAgentState) -> FigureAgentState:
        spec = self.planner.plan(state["request"], state["dataset"], state.get("context", ""))
        if not spec.image_paths and state["request"].sketch_files:
            spec.image_paths = [str(path.resolve()) for path in state["request"].sketch_files]
        return {"spec": spec, "warnings": self.planner.take_warnings()}

    def generate_code(self, state: FigureAgentState) -> FigureAgentState:
        output_dir = Path(state["output_dir"])
        code_files = generate_code_bundle(state["spec"], state["request"], state["dataset"], output_dir)
        return {"code_files": code_files}

    def render(self, state: FigureAgentState) -> FigureAgentState:
        python_file = state["code_files"].get("python")
        if not python_file:
            raise ValueError("Python code output is required because it is the controlled rendering backend")
        result = execute_python_renderer(Path(python_file), Path(state["output_dir"]), self.settings)
        return {"render_result": result}

    def inspect(self, state: FigureAgentState) -> FigureAgentState:
        report = inspect_artifacts(
            state["spec"],
            state["request"],
            Path(state["output_dir"]),
            state.get("revision", 0),
        )
        return {"quality_report": report}

    def revise(self, state: FigureAgentState) -> FigureAgentState:
        spec = state["spec"].model_copy(deep=True)
        spec.width_inches = min(16, spec.width_inches * 1.15)
        spec.height_inches = min(12, spec.height_inches * 1.15)
        spec.dpi = max(300, spec.dpi)
        return {
            "spec": spec,
            "revision": state.get("revision", 0) + 1,
            "warnings": ["Initial quality inspection failed; layout was enlarged and rendered once more."],
        }

    def finalize(self, state: FigureAgentState) -> FigureAgentState:
        request = state["request"]
        output_dir = Path(state["output_dir"])
        caption_payload = self.planner.captions(state["spec"], state["dataset"], offline=request.offline)
        captions = CaptionSet.model_validate(caption_payload)
        caption_files: dict[str, str] = {}
        for language in request.languages:
            path = output_dir / f"caption_{language}.txt"
            path.write_text(getattr(captions, language), encoding="utf-8")
            caption_files[language] = str(path)

        figures = {
            language: {
                extension: str(output_dir / f"figure_{language}.{extension}")
                for extension in request.export_formats
            }
            for language in request.languages
        }
        manifest = ArtifactManifest(
            output_dir=output_dir,
            figures=figures,
            code=state["code_files"],
            captions=caption_files,
            data_file=str(state["dataset"].normalized_path) if state["dataset"].normalized_path else None,
            config_file=str(output_dir / "figure_config.json"),
            quality_report_file=str(output_dir / "quality_report.json"),
        )
        manifest_path = output_dir / "manifest.json"
        manifest.manifest_file = str(manifest_path)
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        (output_dir / "request.json").write_text(request.model_dump_json(indent=2), encoding="utf-8")
        return {
            "captions": captions,
            "artifacts": manifest,
            "warnings": self.planner.take_warnings(),
        }

    def _resolve_output_dir(self, request: FigureRequest) -> Path:
        if request.output_dir:
            output_dir = request.output_dir.expanduser().resolve()
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            base = self.settings.output_dir
            if not base.is_absolute():
                base = Path.cwd() / base
            output_dir = (base / stamp).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir


def route_after_inspection(state: FigureAgentState) -> str:
    report = state["quality_report"]
    return "finalize" if report.passed or state.get("revision", 0) >= 1 else "revise"
