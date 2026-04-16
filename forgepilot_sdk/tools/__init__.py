from forgepilot_sdk.tools.base import define_tool, defineTool, to_api_tool, toApiTool
from forgepilot_sdk.tools.core import (
    clear_agents,
    clear_config,
    clear_cron_jobs,
    clear_mailboxes,
    clear_question_handler,
    clear_tasks,
    clear_teams,
    clear_todos,
    get_all_cron_jobs,
    get_all_tasks,
    get_all_teams,
    get_config,
    get_current_plan,
    get_task,
    get_team,
    get_todos,
    is_plan_mode_active,
    read_mailbox,
    register_agents,
    set_config,
    set_deferred_tools,
    set_mcp_connections,
    set_question_handler,
    write_to_mailbox,
)
from forgepilot_sdk.tools.registry import (
    assembleToolPool,
    assemble_tool_pool,
    filterTools,
    filter_tools,
    getAllBaseTools,
    get_all_base_tools,
)

_BASE_TOOL_BY_NAME = {tool.name: tool for tool in get_all_base_tools()}

# Individual built-in tool exports (TypeScript-style names).
BashTool = _BASE_TOOL_BY_NAME["Bash"]
FileReadTool = _BASE_TOOL_BY_NAME["Read"]
FileWriteTool = _BASE_TOOL_BY_NAME["Write"]
FileEditTool = _BASE_TOOL_BY_NAME["Edit"]
GlobTool = _BASE_TOOL_BY_NAME["Glob"]
GrepTool = _BASE_TOOL_BY_NAME["Grep"]
NotebookEditTool = _BASE_TOOL_BY_NAME["NotebookEdit"]
WebFetchTool = _BASE_TOOL_BY_NAME["WebFetch"]
WebSearchTool = _BASE_TOOL_BY_NAME["WebSearch"]
AgentTool = _BASE_TOOL_BY_NAME["Agent"]
SendMessageTool = _BASE_TOOL_BY_NAME["SendMessage"]
TeamCreateTool = _BASE_TOOL_BY_NAME["TeamCreate"]
TeamDeleteTool = _BASE_TOOL_BY_NAME["TeamDelete"]
TaskCreateTool = _BASE_TOOL_BY_NAME["TaskCreate"]
TaskListTool = _BASE_TOOL_BY_NAME["TaskList"]
TaskUpdateTool = _BASE_TOOL_BY_NAME["TaskUpdate"]
TaskGetTool = _BASE_TOOL_BY_NAME["TaskGet"]
TaskStopTool = _BASE_TOOL_BY_NAME["TaskStop"]
TaskOutputTool = _BASE_TOOL_BY_NAME["TaskOutput"]
EnterWorktreeTool = _BASE_TOOL_BY_NAME["EnterWorktree"]
ExitWorktreeTool = _BASE_TOOL_BY_NAME["ExitWorktree"]
EnterPlanModeTool = _BASE_TOOL_BY_NAME["EnterPlanMode"]
ExitPlanModeTool = _BASE_TOOL_BY_NAME["ExitPlanMode"]
AskUserQuestionTool = _BASE_TOOL_BY_NAME["AskUserQuestion"]
ToolSearchTool = _BASE_TOOL_BY_NAME["ToolSearch"]
ListMcpResourcesTool = _BASE_TOOL_BY_NAME["ListMcpResources"]
ReadMcpResourceTool = _BASE_TOOL_BY_NAME["ReadMcpResource"]
CronCreateTool = _BASE_TOOL_BY_NAME["CronCreate"]
CronDeleteTool = _BASE_TOOL_BY_NAME["CronDelete"]
CronListTool = _BASE_TOOL_BY_NAME["CronList"]
RemoteTriggerTool = _BASE_TOOL_BY_NAME["RemoteTrigger"]
LSPTool = _BASE_TOOL_BY_NAME["LSP"]
ConfigTool = _BASE_TOOL_BY_NAME["Config"]
TodoWriteTool = _BASE_TOOL_BY_NAME["TodoWrite"]
SkillTool = _BASE_TOOL_BY_NAME["Skill"]

# camelCase aliases for management helpers.
registerAgents = register_agents
clearAgents = clear_agents
readMailbox = read_mailbox
writeToMailbox = write_to_mailbox
clearMailboxes = clear_mailboxes
getAllTasks = get_all_tasks
getTask = get_task
clearTasks = clear_tasks
getAllTeams = get_all_teams
getTeam = get_team
clearTeams = clear_teams
isPlanModeActive = is_plan_mode_active
getCurrentPlan = get_current_plan
setQuestionHandler = set_question_handler
clearQuestionHandler = clear_question_handler
setDeferredTools = set_deferred_tools
setMcpConnections = set_mcp_connections
getAllCronJobs = get_all_cron_jobs
clearCronJobs = clear_cron_jobs
getConfig = get_config
setConfig = set_config
clearConfig = clear_config
getTodos = get_todos
clearTodos = clear_todos

__all__ = [
    "define_tool",
    "defineTool",
    "to_api_tool",
    "toApiTool",
    "BashTool",
    "FileReadTool",
    "FileWriteTool",
    "FileEditTool",
    "GlobTool",
    "GrepTool",
    "NotebookEditTool",
    "WebFetchTool",
    "WebSearchTool",
    "AgentTool",
    "SendMessageTool",
    "TeamCreateTool",
    "TeamDeleteTool",
    "TaskCreateTool",
    "TaskListTool",
    "TaskUpdateTool",
    "TaskGetTool",
    "TaskStopTool",
    "TaskOutputTool",
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "EnterPlanModeTool",
    "ExitPlanModeTool",
    "AskUserQuestionTool",
    "ToolSearchTool",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
    "CronCreateTool",
    "CronDeleteTool",
    "CronListTool",
    "RemoteTriggerTool",
    "LSPTool",
    "ConfigTool",
    "TodoWriteTool",
    "SkillTool",
    "get_all_base_tools",
    "getAllBaseTools",
    "filter_tools",
    "filterTools",
    "assemble_tool_pool",
    "assembleToolPool",
    "register_agents",
    "registerAgents",
    "clear_agents",
    "clearAgents",
    "read_mailbox",
    "readMailbox",
    "write_to_mailbox",
    "writeToMailbox",
    "clear_mailboxes",
    "clearMailboxes",
    "get_all_tasks",
    "getAllTasks",
    "get_task",
    "getTask",
    "clear_tasks",
    "clearTasks",
    "get_all_teams",
    "getAllTeams",
    "get_team",
    "getTeam",
    "clear_teams",
    "clearTeams",
    "is_plan_mode_active",
    "isPlanModeActive",
    "get_current_plan",
    "getCurrentPlan",
    "set_question_handler",
    "setQuestionHandler",
    "clear_question_handler",
    "clearQuestionHandler",
    "set_deferred_tools",
    "setDeferredTools",
    "set_mcp_connections",
    "setMcpConnections",
    "get_all_cron_jobs",
    "getAllCronJobs",
    "clear_cron_jobs",
    "clearCronJobs",
    "get_config",
    "getConfig",
    "set_config",
    "setConfig",
    "clear_config",
    "clearConfig",
    "get_todos",
    "getTodos",
    "clear_todos",
    "clearTodos",
]

