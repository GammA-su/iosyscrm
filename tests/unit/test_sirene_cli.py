"""Surface CLI du lot T4."""

from typer.testing import CliRunner

from app.cli import app

runner = CliRunner()


def test_sirene_group_exposes_the_three_commands() -> None:
    result = runner.invoke(app, ["sirene", "--help"])

    assert result.exit_code == 0
    assert "sync" in result.stdout
    assert "backfill" in result.stdout
    assert "status" in result.stdout


def test_enrich_group_exposes_the_three_commands() -> None:
    result = runner.invoke(app, ["enrich", "--help"])

    assert result.exit_code == 0
    assert "company" in result.stdout
    assert "batch" in result.stdout
    assert "stats" in result.stdout


def test_enrich_batch_size_must_be_positive() -> None:
    result = runner.invoke(app, ["enrich", "batch", "--size", "0"])

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_score_group_exposes_the_two_commands() -> None:
    result = runner.invoke(app, ["score", "--help"])

    assert result.exit_code == 0
    assert "rebuild" in result.stdout
    assert "explain" in result.stdout


def test_score_rebuild_batch_size_must_be_positive() -> None:
    result = runner.invoke(app, ["score", "rebuild", "--batch-size", "0"])

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr


def test_backfill_days_must_be_positive() -> None:
    result = runner.invoke(app, ["sirene", "backfill", "--days", "0"])

    assert result.exit_code == 2
    assert "Invalid value" in result.stderr
