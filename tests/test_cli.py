from kinby.cli import main


def test_kinby_version_prints_the_package_version(capsys):
    exit_code = main(["--version"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "0.1.0" in captured.out
