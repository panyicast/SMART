# STK 11.6 操作 skill

## Skill ID

`smart.skill.stk_11_6_operations`

## 目的

面向本机 STK 11.6 桌面程序、STKX/内嵌场景、COM Object Model 和 Connect 命令提供操作方案、命令复核和帮助文档检索入口。

## 能力

- 默认使用 STK 11.6，不混用 STK 12.2 才有的行为。
- 通过 COM/Object Model 或 `localhost:5001` Socket Connect 操作已打开的 STK 桌面场景。
- 复核 Connect 命令、对象路径、DataProvider、Astrogator/MCS 和外部星历导入流程。
- 优先检索本机 STK 11.6 帮助索引和安装目录下完整帮助文档。
- 生成可执行的 STK 操作步骤、Connect 命令草案和故障排查建议。

## 本机 STK 11.6 资料入口

- STK 11.6 runtime root: `D:\Program Files\AGI\STK 116`
- STK 11.6 help root: `D:\Program Files\AGI\STK 116\Help`
- General help: `D:\Program Files\AGI\STK 116\Help\index.htm`
- Programming help: `D:\Program Files\AGI\STK 116\Help\Programming\index.htm`
- Connect help: `D:\Program Files\AGI\STK 116\Help\Programming\Subsystems\connect\connect.htm`
- Connect command help: `D:\Program Files\AGI\STK 116\Help\Programming\Subsystems\connectCmds\connectCmds.htm`
- Release notes: `D:\Program Files\AGI\STK 116\Help\releaseNotes.chm`
- Local config file: set `SMART_STK_HELP_CONFIG`, or use the default `~/.smart/stk_help.json`.
- Config JSON keys: `kb_path`, `script_path`, `command`.
- Global KB: set `SMART_STK_HELP_KB` or config `kb_path` to the local `stk11_help.sqlite3` path.
- Preferred KB command: `stkhelp "<query>"`, `SMART_STKHELP_COMMAND`, or config `command`.
- Fallback KB script: set `SMART_STK_HELP_SCRIPT` or config `script_path` to the local `stkhelp_cli.py` path.

## 主要工具

- `query_stk_help`
- `inspect_project_files`

## 输出

- STK 11.6 操作步骤。
- Connect 命令和 COM/Object Model 调用建议。
- 本机帮助文档路径或 KB 检索关键词。
- STK 场景/桌面状态检查清单。

## 约束

- 不关闭 STK 场景或退出 STK，除非用户明确要求。
- 修改场景前先建议读取对象列表、场景目录和当前设置。
- 命令语法不确定时必须先查 STK 11.6 本机帮助或 KB。
- 如果 `stkhelp` 命令不可用，应提示设置 `SMART_STK_HELP_CONFIG`、`SMART_STK_HELP_KB`、`SMART_STK_HELP_SCRIPT`，或检查 `stkhelp` 是否在命令搜索目录列表（PATH）中。
