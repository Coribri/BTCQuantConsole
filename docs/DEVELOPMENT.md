# Development / 开发说明

## Layout / 目录

```text
btcquant/           Python application and static UI
tests/              Isolated Python and JavaScript tests
assets/             Application icon
examples/           External-model example
packaging/          Windows executable build specification
run_console.py      Desktop package entry point
config.toml         Default local configuration
VERSION             Public release version
```

## Tests / 测试

```powershell
python -m pip install -e .
python -m pip install pytest httpx
python -m pytest
node --test tests/test_drawing_coordinates.cjs tests/test_quant_guide.cjs tests/test_volume_pane.cjs
```

Some DOM tests also require jsdom. Install the test-only dependency with:

```powershell
npm install --prefix tests/ui --no-audit --no-fund jsdom
node --test tests/live-ticket.test.cjs tests/live-ui.test.cjs
```

测试使用临时目录和桩数据。不要给测试注入真实 API 凭据。  
Tests use temporary directories and fixture data. Do not inject real exchange credentials into tests.

## Windows package / Windows 打包

```powershell
python -m pip install pyinstaller
python -m PyInstaller --noconfirm packaging/BTCQuantConsole.spec
```

Use `dist/BTCQuantConsole` as the executable package. Copy in `config.toml`, `Start-PAPER.bat` and `Start-LIVE.bat`. Exclude `.env`, `runtime`, local databases, logs and account-related files from every release archive.

源码中的版本号为 1.0.0，公开发布版本和 Git 标签使用 1.0。  
The source package version is 1.0.0; the public release version and Git tag are 1.0.
