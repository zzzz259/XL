"""Execute the actual producer entry method without loading media dependencies."""

import ast
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class BaselineEligibilityTests(unittest.TestCase):
    def test_initial_baseline_even_warning_or_failure_is_ineligible(self):
        tree = ast.parse(
            (Path(__file__).parents[1] / "server_app/processor.py").read_text(
                encoding="utf-8"
            )
        )
        cls = next(
            n
            for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "ProductionUpdateProcessor"
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "__call__"
        )
        method.returns = None
        for arg in method.args.args:
            arg.annotation = None
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        for current in (None, {}, {"version": 1}):
            for failure in (False, True):
                with self.subTest(current=current, failure=failure):
                    bindings = []
                    context = types.SimpleNamespace(
                        bind_target=lambda *a, bindings=bindings, **k: bindings.append(
                            k
                        )
                    )
                    namespace = {
                        "__package__": "server_app",
                        "__name__": "server_app.processor",
                        "read_current_pointer": lambda p: None,
                        "_load_current": lambda p, current=current: current,
                    }
                    exec(compile(module, "processor.__call__", "exec"), namespace)  # noqa: S102 - executes the actual isolated producer method

                    def process(*args, failure=failure):
                        if failure:
                            raise RuntimeError("metadata failure")
                        return types.SimpleNamespace(new_character_count=0)

                    obj = types.SimpleNamespace(
                        client=types.SimpleNamespace(
                            fetch_update_info=lambda: types.SimpleNamespace(timestamp=2)
                        ),
                        publication=None,
                        config=types.SimpleNamespace(data_dir=Path(".")),
                        _write_update_status=lambda **kw: None,
                        _process=process,
                    )
                    with patch(
                        "server_app.portraits.core_outcomes.current_run",
                        return_value=context,
                    ):
                        if failure:
                            with self.assertRaises(RuntimeError):
                                namespace["__call__"](obj, Path("."))
                        else:
                            namespace["__call__"](obj, Path("."))
                    self.assertEqual(
                        current is not None, bindings[0]["announcement_eligible"]
                    )


class DurableProducerEligibilityTests(unittest.TestCase):
    """Linux native journal/pipeline evidence, isolated from consumer imports."""

    def test_actual_producer_pipeline_persists_baseline_warning_failure_eligibility(
        self,
    ):
        import json
        import os
        import subprocess
        import sys

        if os.name != "posix":
            self.skipTest("actual CoreJournal directory durability requires Linux")
        for existing in (False, True):
            for mode in ("success", "warning", "failure"):
                with self.subTest(existing=existing, mode=mode):
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            "--producer-child",
                            "existing" if existing else "baseline",
                            mode,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=45,
                    )
                    outcome = json.loads(completed.stdout)
                    self.assertEqual(existing, outcome["announcement_eligible"])
                    self.assertEqual("processed", outcome["classification"])
                    self.assertEqual("2", outcome["version"])
                    self.assertEqual(
                        "failed" if mode == "failure" else "published", outcome["phase"]
                    )
                    if mode == "warning":
                        self.assertEqual(["core_optional_warning"], outcome["warnings"])
                        self.assertEqual(1, outcome["counts"]["card_warning_count"])
                    if mode != "failure":
                        self.assertEqual(
                            "no-new-output" if existing else "baseline",
                            outcome["cards"]["reason"],
                        )


def _producer_child(existing, mode):
    """Only this fresh child imports the actual processor/media dependency tree."""
    import json
    import os
    import tempfile
    from types import SimpleNamespace

    from server_app.pipeline import ProcessResult, UpdatePipeline
    from server_app.portraits.core_outcomes import CoreJournal, CoreReader
    from server_app.processor import ProductionUpdateProcessor

    os.umask(0o077)
    with tempfile.TemporaryDirectory(
        dir="/var/tmp", prefix="portrait-consumer-producer-"
    ) as temporary:
        root = Path(temporary)
        if existing:
            (root / "character_data").mkdir()
            (root / "character_data" / "current.json").write_text(
                json.dumps({"version": 1, "characters": []}), encoding="utf-8"
            )
        config = SimpleNamespace(
            data_dir=root,
            cdn_base="https://fixture.invalid",
            instance_id="test",
            portrait_sources_enabled=True,
            portrait_source_id="baseline-test",
        )
        processor = ProductionUpdateProcessor(config)
        publication = processor.publication
        if not publication.acquire_run():
            raise RuntimeError("isolated fixture unexpectedly failed to acquire owner")
        try:
            journal = CoreJournal(publication)
            context = journal.begin_run("actual-producer", trigger="scheduled")

            def process(staging, _update_info, current):
                if (current is not None) != existing:
                    raise AssertionError("actual processor loaded wrong baseline input")
                if mode == "failure":
                    raise RuntimeError("fixture processing failure")
                (staging / "character_data" / "current.json").write_text(
                    json.dumps({"version": 2, "characters": []}), encoding="utf-8"
                )
                return ProcessResult(
                    version_timestamp=2,
                    processed=True,
                    schedule_enabled=False,
                    warnings=("fixture_optional_warning",) if mode == "warning" else (),
                    card_warning_count=1 if mode == "warning" else 0,
                    card_intent={
                        "kind": "none",
                        "reason": "no-new-output" if existing else "baseline",
                    },
                    portrait_source_state="not_requested",
                )

            with (
                context.activate(),
                patch.object(
                    processor.client,
                    "fetch_update_info",
                    return_value=SimpleNamespace(timestamp=2),
                ),
                patch.object(processor, "_process", side_effect=process),
            ):
                result = UpdatePipeline(root, processor).run_once()
            if bool(result.error) != (mode == "failure"):
                raise AssertionError(f"unexpected real pipeline result: {result}")
            outcome = CoreReader(journal.path, scope=journal.scope).read_run(
                context.run_id
            )
            if outcome is None:
                raise AssertionError("actual durable Core outcome missing")
            print(json.dumps(outcome))
        finally:
            publication.release_run()


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 4 and sys.argv[1] == "--producer-child":
        _producer_child(sys.argv[2] == "existing", sys.argv[3])
    else:
        unittest.main()
