from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automan_core import cli


class CliContractTest(unittest.TestCase):
    def test_validate_prints_pigsty_style_status_tags(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        output = io.StringIO()

        with patch("sys.argv", ["automan", "validate", "-i", str(repo / "conf" / "tpcc" / "pg.yml")]):
            with redirect_stdout(output):
                cli.main()

        text = output.getvalue()
        self.assertIn("[ OK ]", text)
        self.assertIn("[HINT]", text)
        self.assertIn("manual-only", text)

    def test_param_generates_manual_commands_without_execution(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _copy_minimal_repo_files(repo, root)
            conf_dir = root / "conf" / "tpcc"
            conf_dir.mkdir(parents=True)
            shutil.copyfile(repo / "conf" / "tpcc" / "pg.yml", conf_dir / "pg.yml")
            output = io.StringIO()

            with patch("sys.argv", ["automan", "param", "-i", str(conf_dir / "pg.yml")]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.main()

            text = output.getvalue()
            self.assertIn("[ OK ]", text)
            self.assertIn("manual-parameter-commands.sh", text)
            scripts = list((root / "runs" / "campaigns").glob("*/manual-parameter-commands.sh"))
            self.assertEqual(len(scripts), 1)
            self.assertIn("automan does not execute", scripts[0].read_text(encoding="utf-8"))

    def test_configure_copies_template(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "conf" / "tpcc").mkdir(parents=True)
            shutil.copyfile(repo / "conf" / "tpcc" / "pg.yml", root / "conf" / "tpcc" / "pg.yml")
            output = io.StringIO()

            with patch("sys.argv", ["configure", "-c", "tpcc/pg", "-o", "automan.yml"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        cli.configure_main()

            self.assertTrue((root / "automan.yml").exists())
            self.assertIn("[ OK ]", output.getvalue())
            self.assertIn("tpcc/pg", output.getvalue())

    def test_legacy_run_task_remains_available(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["run", "--task", "configs/tasks/tpcc-postgresql-template.yaml", "--plan-only"])
        self.assertEqual(args.command, "run")
        self.assertEqual(args.task, "configs/tasks/tpcc-postgresql-template.yaml")
        self.assertTrue(args.plan_only)

    def test_report_missing_campaign_prints_fail_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = io.StringIO()

            with patch("sys.argv", ["automan", "report", "--campaign", "missing"]):
                with patch.object(cli.Path, "cwd", return_value=root):
                    with redirect_stdout(output):
                        with self.assertRaises(SystemExit):
                            cli.main()

            text = output.getvalue()
            self.assertIn("[FAIL]", text)
            self.assertIn("campaign missing", text)


def _copy_minimal_repo_files(src: Path, dst: Path) -> None:
    for relative in [
        "configs/database-profiles/postgresql/heap-single-node.yaml",
        "benchmarks/tpcc/benchmarksql/props.template",
    ]:
        target = dst / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((src / relative).read_text(encoding="utf-8"), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
