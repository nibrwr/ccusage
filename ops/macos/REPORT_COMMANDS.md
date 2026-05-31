# Report Commands

```sh
cd ~/Development/ccusage
git pull
source ops/macos/fleet-roots/icloud.env
```

## Combined Tables

```sh
ops/macos/report-codex-fleet.zsh daily
ops/macos/report-codex-fleet.zsh monthly
ops/macos/report-codex-fleet.zsh session
```

## By-Mac Tables

```sh
ops/macos/report-codex-fleet-by-machine.zsh daily
ops/macos/report-codex-fleet-by-machine.zsh monthly
ops/macos/report-codex-fleet-by-machine.zsh session
```

## One Mac Tables

```sh
ops/macos/report-codex-fleet.zsh --machine mac-mini-m4-1 daily
ops/macos/report-codex-fleet.zsh --machine mac-mini-m4-1 monthly
ops/macos/report-codex-fleet.zsh --machine macbook-air-m4 daily
ops/macos/report-codex-fleet.zsh --machine macbook-air-m4 monthly
```

## Date Filters

```sh
ops/macos/report-codex-fleet.zsh daily --since 2026-05-01 --until 2026-05-31
ops/macos/report-codex-fleet.zsh monthly --since 2026-05-01
ops/macos/report-codex-fleet-by-machine.zsh daily --since 2026-05-01 --until 2026-05-31
ops/macos/report-codex-fleet-by-machine.zsh monthly --since 2026-05-01
```

## JSON Tables

```sh
ops/macos/report-codex-fleet.zsh daily --json
ops/macos/report-codex-fleet.zsh monthly --json
ops/macos/report-codex-fleet.zsh --machine mac-mini-m4-1 daily --json
ops/macos/report-codex-fleet.zsh --machine macbook-air-m4 daily --json
```

