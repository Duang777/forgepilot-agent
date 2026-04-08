import { useEffect, useState } from 'react';
import { getClaudeSkillsDir } from '@/shared/lib/paths';
import { cn } from '@/shared/lib/utils';
import { useLanguage } from '@/shared/providers/language-provider';
import {
  ArrowLeftRight,
  ChevronDown,
  FolderOpen,
  Github,
  Layers,
  Loader2,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react';

import { Switch } from '../components/Switch';
import { API_BASE_URL } from '../constants';
import type { SettingsTabProps, SkillInfo } from '../types';

// Parse YAML frontmatter from SKILL.md
function parseSkillMdFrontmatter(content: string): {
  name?: string;
  description?: string;
} {
  const frontmatterMatch = content.match(/^---\s*\n([\s\S]*?)\n---/);
  if (!frontmatterMatch) return {};

  const frontmatter = frontmatterMatch[1];
  const result: { name?: string; description?: string } = {};

  // Parse name
  const nameMatch = frontmatter.match(/^name:\s*(.+)$/m);
  if (nameMatch) {
    result.name = nameMatch[1].trim();
  }

  // Parse description
  const descMatch = frontmatter.match(/^description:\s*(.+)$/m);
  if (descMatch) {
    result.description = descMatch[1].trim();
  }

  return result;
}

// Helper function to open folder in system file manager
const openFolderInSystem = async (folderPath: string) => {
  try {
    const response = await fetch(`${API_BASE_URL}/files/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: folderPath, expandHome: true }),
    });
    const data = await response.json();
    if (!data.success) {
      console.error('[Skills] Failed to open folder:', data.error);
    }
  } catch (err) {
    console.error('[Skills] Error opening folder:', err);
  }
};

// Skill card component
function SkillCard({
  skill,
  onDelete,
}: {
  skill: SkillInfo;
  onDelete: () => void;
}) {
  const { t } = useLanguage();
  const [showMenu, setShowMenu] = useState(false);

  return (
    <div className="border-border bg-background hover:border-foreground/20 relative flex flex-col rounded-xl border p-4 transition-colors">
      <div className="mb-2">
        <span className="text-foreground text-sm font-medium">
          {skill.name}
        </span>
      </div>

      <p className="text-muted-foreground mb-4 line-clamp-2 flex-1 text-xs">
        {skill.description || t.settings.skillsNoDescription}
      </p>

      <div className="border-border flex items-center justify-end border-t pt-3">
        <div className="relative">
          <button
            onClick={() => setShowMenu(!showMenu)}
            className="text-muted-foreground hover:bg-accent hover:text-foreground rounded p-1 transition-colors"
          >
            <MoreHorizontal className="size-4" />
          </button>
          {showMenu && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setShowMenu(false)}
              />
              <div className="border-border bg-popover absolute right-0 bottom-full z-20 mb-1 min-w-max rounded-lg border py-1 shadow-lg">
                <button
                  onClick={() => {
                    openFolderInSystem(skill.path);
                    setShowMenu(false);
                  }}
                  className="hover:bg-accent flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm whitespace-nowrap transition-colors"
                >
                  <FolderOpen className="size-3.5 shrink-0" />
                  {t.settings.skillsOpenFolder}
                </button>
                <button
                  onClick={() => {
                    onDelete();
                    setShowMenu(false);
                  }}
                  className="hover:bg-destructive/10 text-destructive flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm whitespace-nowrap transition-colors"
                >
                  <Trash2 className="size-3.5 shrink-0" />
                  {t.settings.skillsDelete}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

type MainTab = 'installed' | 'settings';

export function SkillsSettings({
  settings,
  onSettingsChange,
}: SettingsTabProps) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [mainTab, setMainTab] = useState<MainTab>('installed');
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [skillsDirs, setSkillsDirs] = useState<{
    user: string;
    app: string;
  }>({ user: '', app: '' });
  const [defaultSkillsPath, setDefaultSkillsPath] = useState('');
  const [showAddMenu, setShowAddMenu] = useState(false);

  // Load default skills path on mount (platform-aware)
  useEffect(() => {
    getClaudeSkillsDir().then(setDefaultSkillsPath);
  }, []);
  const [showGitHubImport, setShowGitHubImport] = useState(false);
  const [githubUrl, setGithubUrl] = useState('');
  const [githubBranch, setGithubBranch] = useState('');
  const [githubPath, setGithubPath] = useState('');
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState('');
  const [selfChecking, setSelfChecking] = useState(false);
  const [selfCheckStatus, setSelfCheckStatus] = useState<
    'idle' | 'success' | 'error'
  >('idle');
  const [selfCheckMessage, setSelfCheckMessage] = useState('');
  const { t, tt } = useLanguage();

  const isSkillConfigured = (skill: SkillInfo) => {
    return skill.files.length > 0;
  };

  // Filter and sort skills
  const filteredSkills = skills
    .filter((skill) => {
      // Filter by search query
      if (
        searchQuery &&
        !skill.name.toLowerCase().includes(searchQuery.toLowerCase())
      ) {
        return false;
      }
      return true;
    })
    .sort((a, b) => {
      const aConfigured = isSkillConfigured(a);
      const bConfigured = isSkillConfigured(b);
      if (a.enabled && aConfigured && !(b.enabled && bConfigured)) return -1;
      if (b.enabled && bConfigured && !(a.enabled && aConfigured)) return 1;
      if (aConfigured && !bConfigured) return -1;
      if (bConfigured && !aConfigured) return 1;
      return 0;
    });

  const loadSkillsFromPath = async (skillsPath: string) => {
    setLoading(true);
    try {
      // Get all skills directories (forgepilot and claude)
      const dirsResponse = await fetch(`${API_BASE_URL}/files/skills-dir`);
      const dirsData = await dirsResponse.json();

      const allSkills: SkillInfo[] = [];

      // Save directory paths
      const dirs: { user: string; app: string } = { user: '', app: '' };
      if (dirsData.directories) {
        for (const dir of dirsData.directories as {
          name: string;
          path: string;
          exists: boolean;
        }[]) {
          if (dir.name === 'claude') {
            dirs.user = dir.path;
          } else if (dir.name === 'forgepilot') {
            dirs.app = dir.path;
          }
        }
      }
      setSkillsDirs(dirs);

      // Load skills from user directory only (claude)
      if (dirsData.directories) {
        for (const dir of dirsData.directories as {
          name: string;
          path: string;
          exists: boolean;
        }[]) {
          // Only load from user directory (claude), skip app directory (forgepilot)
          if (dir.name !== 'claude' || !dir.exists) continue;

          try {
            const filesResponse = await fetch(`${API_BASE_URL}/files/readdir`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: dir.path, maxDepth: 3 }),
            });
            const filesData = await filesResponse.json();

            if (filesData.success && filesData.files) {
              for (const folder of filesData.files) {
                if (folder.isDir) {
                  // Read SKILL.md for name and description
                  let skillName = folder.name;
                  let description = '';
                  try {
                    const skillMdPath = `${folder.path}/SKILL.md`;
                    const mdResponse = await fetch(
                      `${API_BASE_URL}/files/read`,
                      {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: skillMdPath }),
                      }
                    );
                    const mdData = await mdResponse.json();
                    if (mdData.success && mdData.content) {
                      const frontmatter = parseSkillMdFrontmatter(
                        mdData.content
                      );
                      if (frontmatter.name) {
                        skillName = frontmatter.name;
                      }
                      if (frontmatter.description) {
                        description = frontmatter.description;
                      }
                    }
                  } catch {
                    // Ignore errors reading SKILL.md
                  }

                  allSkills.push({
                    id: `${dir.name}-${folder.name}`,
                    name: skillName,
                    source: dir.name as 'claude' | 'forgepilot',
                    path: folder.path,
                    files: folder.children || [],
                    enabled: true,
                    description,
                  });
                }
              }
            }
          } catch (err) {
            console.error(
              `[Skills] Failed to load skills from ${dir.name}:`,
              err
            );
          }
        }
      }

      // Also load from user-configured skillsPath if different from default directories
      if (skillsPath) {
        const isDefaultDir = dirsData.directories?.some(
          (d: { path: string }) => d.path === skillsPath
        );
        if (!isDefaultDir) {
          try {
            const filesResponse = await fetch(`${API_BASE_URL}/files/readdir`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: skillsPath, maxDepth: 3 }),
            });
            const filesData = await filesResponse.json();

            if (filesData.success && filesData.files) {
              for (const folder of filesData.files) {
                if (folder.isDir) {
                  // Read SKILL.md for name and description
                  let skillName = folder.name;
                  let description = '';
                  try {
                    const skillMdPath = `${folder.path}/SKILL.md`;
                    const mdResponse = await fetch(
                      `${API_BASE_URL}/files/read`,
                      {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ path: skillMdPath }),
                      }
                    );
                    const mdData = await mdResponse.json();
                    if (mdData.success && mdData.content) {
                      const frontmatter = parseSkillMdFrontmatter(
                        mdData.content
                      );
                      if (frontmatter.name) {
                        skillName = frontmatter.name;
                      }
                      if (frontmatter.description) {
                        description = frontmatter.description;
                      }
                    }
                  } catch {
                    // Ignore errors reading SKILL.md
                  }

                  allSkills.push({
                    id: `custom-${folder.name}`,
                    name: skillName,
                    source: 'forgepilot',
                    path: folder.path,
                    files: folder.children || [],
                    enabled: true,
                    description,
                  });
                }
              }
            }
          } catch (err) {
            console.error(
              '[Skills] Failed to load skills from custom path:',
              err
            );
          }
        }
      }

      setSkills(allSkills);
    } catch (err) {
      console.error('[Skills] Failed to load skills:', err);
      setSkills([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSkillsFromPath(settings.skillsPath);
  }, [settings.skillsPath]);

  // Initialize platform-aware default path
  useEffect(() => {
    getClaudeSkillsDir().then(setDefaultSkillsPath);
  }, []);

  const [deleteDialogSkill, setDeleteDialogSkill] = useState<SkillInfo | null>(
    null
  );

  const handleDeleteSkill = (skillId: string) => {
    const skill = skills.find((s) => s.id === skillId);
    if (skill) {
      setDeleteDialogSkill(skill);
    }
  };

  const handleOpenSkillFolder = () => {
    if (deleteDialogSkill) {
      openFolderInSystem(deleteDialogSkill.path);
      setDeleteDialogSkill(null);
    }
  };

  const runImportSelfCheck = async () => {
    setSelfChecking(true);
    setSelfCheckStatus('idle');
    setSelfCheckMessage('');
    try {
      const response = await fetch(`${API_BASE_URL}/files/import-skill/self-check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await response.json();
      if (data.success) {
        const count = Number(data.count || 0);
        setSelfCheckStatus('success');
        setSelfCheckMessage(
          tt('settings.skillsImportSelfCheckSuccess', { count })
        );
      } else {
        const message = String(data.error || t.settings.skillsImportSelfCheckFailed);
        setSelfCheckStatus('error');
        setSelfCheckMessage(message);
      }
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t.settings.skillsImportSelfCheckFailed;
      setSelfCheckStatus('error');
      setSelfCheckMessage(message);
    } finally {
      setSelfChecking(false);
    }
  };

  if (loading) {
    return (
      <div className="text-muted-foreground flex h-full items-center justify-center gap-2">
        <Loader2 className="size-4 animate-spin" />
        {t.common.loading}
      </div>
    );
  }

  return (
    <div className="-m-6 flex h-[calc(100%+48px)] flex-col">
      {/* Tab Bar */}
      <div className="border-border shrink-0 border-b px-6">
        <div className="flex items-center gap-6">
          <button
            onClick={() => setMainTab('installed')}
            className={cn(
              'relative py-4 text-sm font-medium transition-colors',
              mainTab === 'installed'
                ? 'text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t.settings.skillsInstalled}
            {mainTab === 'installed' && (
              <span className="bg-foreground absolute bottom-0 left-0 h-0.5 w-full" />
            )}
          </button>
          <button
            onClick={() => setMainTab('settings')}
            className={cn(
              'relative py-4 text-sm font-medium transition-colors',
              mainTab === 'settings'
                ? 'text-foreground'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t.settings.title}
            {mainTab === 'settings' && (
              <span className="bg-foreground absolute bottom-0 left-0 h-0.5 w-full" />
            )}
          </button>
        </div>
      </div>

      {/* Content Area */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {mainTab === 'installed' ? (
          /* Installed Tab Content */
          <div className="flex h-full flex-col">
            {/* Filter Bar */}
            <div className="bg-background sticky top-0 z-10 flex shrink-0 items-center justify-between gap-4 px-6 pt-6 pb-4">
              <div className="flex items-center gap-3">
                {/* Search Input */}
                <div className="relative">
                  <Search className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={t.settings.skillsSearch}
                    className="border-input bg-background text-foreground placeholder:text-muted-foreground focus:ring-ring h-9 w-64 rounded-lg border py-2 pr-3 pl-9 text-sm focus:ring-2 focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                {/* Add Button with Dropdown */}
                <div className="relative">
                  <button
                    onClick={() => setShowAddMenu(!showAddMenu)}
                    className="bg-foreground text-background hover:bg-foreground/90 flex h-9 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors"
                  >
                    <Plus className="size-4" />
                    {t.settings.add}
                    <ChevronDown className="size-4" />
                  </button>
                  {showAddMenu && (
                    <>
                      <div
                        className="fixed inset-0 z-40"
                        onClick={() => setShowAddMenu(false)}
                      />
                      <div className="border-border bg-popover absolute top-full right-0 z-50 mt-1 min-w-[180px] rounded-xl border py-1 shadow-lg">
                        <button
                          onClick={() => {
                            openFolderInSystem(skillsDirs.user);
                            setShowAddMenu(false);
                          }}
                          className="hover:bg-accent flex w-full items-center gap-3 px-3 py-2 text-left transition-colors"
                        >
                          <FolderOpen className="text-muted-foreground size-4 shrink-0" />
                          <span className="text-foreground text-sm">
                            {t.settings.skillsAddToDirectory}
                          </span>
                        </button>
                        <button
                          onClick={() => {
                            setImportError('');
                            setGithubBranch('');
                            setGithubPath('');
                            setShowGitHubImport(true);
                            setShowAddMenu(false);
                          }}
                          className="hover:bg-accent flex w-full items-center gap-3 px-3 py-2 text-left transition-colors"
                        >
                          <Github className="text-muted-foreground size-4 shrink-0" />
                          <span className="text-foreground text-sm">
                            {t.settings.skillsImportGitHub}
                          </span>
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Skills Grid */}
            <div className="min-h-0 flex-1 overflow-y-auto p-6">
              {filteredSkills.length === 0 ? (
                <div className="text-muted-foreground flex h-32 items-center justify-center text-sm">
                  {searchQuery
                    ? t.settings.skillsNoResults
                    : t.settings.skillsEmpty}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-4">
                  {filteredSkills.map((skill) => (
                    <SkillCard
                      key={skill.id}
                      skill={skill}
                      onDelete={() => handleDeleteSkill(skill.id)}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          /* Settings Tab Content */
          <div className="space-y-4 p-6">
            {/* Global Enable Switch */}
            <div className="border-border bg-background rounded-xl border p-4">
              <div className="flex items-center justify-between">
                <div>
                  <h3 className="text-foreground text-sm font-medium">
                    {t.settings.skillsEnabled}
                  </h3>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t.settings.skillsEnabledDescription}
                  </p>
                </div>
                <Switch
                  checked={settings.skillsEnabled !== false}
                  onChange={(checked) =>
                    onSettingsChange({ ...settings, skillsEnabled: checked })
                  }
                />
              </div>
            </div>

            {/* Skills Directory */}
            <div
              className={cn(
                'border-border bg-background rounded-xl border p-4 transition-opacity',
                settings.skillsEnabled === false && 'opacity-50'
              )}
            >
              <div className="flex items-center justify-between">
                <div className="min-w-0 flex-1">
                  <h3 className="text-foreground text-sm font-medium">
                    {t.settings.skillsSource}
                  </h3>
                  <code className="bg-muted text-muted-foreground mt-2 block truncate rounded px-2 py-1 text-xs">
                    {skillsDirs.user || defaultSkillsPath}
                  </code>
                </div>
                <div className="ml-4 flex shrink-0 items-center gap-2">
                  <button
                    onClick={() => openFolderInSystem(skillsDirs.user)}
                    className="text-muted-foreground hover:text-foreground hover:bg-accent rounded p-2 transition-colors"
                    title={t.settings.skillsOpenFolder}
                  >
                    <FolderOpen className="size-4" />
                  </button>
                </div>
              </div>
            </div>

            {/* Import Self Check */}
            <div
              className={cn(
                'border-border bg-background rounded-xl border p-4 transition-opacity',
                settings.skillsEnabled === false && 'opacity-50'
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-foreground text-sm font-medium">
                    {t.settings.skillsImportSelfCheck}
                  </h3>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {t.settings.skillsImportSelfCheckDesc}
                  </p>
                </div>
                <button
                  onClick={runImportSelfCheck}
                  disabled={selfChecking || settings.skillsEnabled === false}
                  className="border-border hover:bg-accent text-foreground flex h-9 shrink-0 items-center gap-2 rounded-lg border px-3 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {selfChecking ? (
                    <>
                      <Loader2 className="size-4 animate-spin" />
                      {t.settings.skillsImportSelfChecking}
                    </>
                  ) : (
                    t.settings.skillsImportSelfCheck
                  )}
                </button>
              </div>
              {selfCheckMessage && (
                <div
                  className={cn(
                    'mt-3 rounded-lg border px-3 py-2 text-xs',
                    selfCheckStatus === 'success'
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                      : 'border-red-400/50 bg-red-500/10 text-red-200'
                  )}
                >
                  {selfCheckMessage}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Delete Skill Dialog */}
      {deleteDialogSkill && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/50"
            onClick={() => setDeleteDialogSkill(null)}
          />
          <div className="bg-background border-border relative z-10 w-[400px] rounded-xl border p-6 shadow-lg">
            <h3 className="text-foreground mb-2 text-base font-semibold">
              {t.settings.skillsDeleteTitle}
            </h3>
            <p className="text-muted-foreground mb-4 text-sm">
              {t.settings.skillsDeleteDescription}
            </p>
            <div className="bg-muted mb-4 rounded-lg p-3">
              <code className="text-foreground text-xs break-all">
                {deleteDialogSkill.path}
              </code>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteDialogSkill(null)}
                className="border-border hover:bg-accent h-9 rounded-lg border px-4 text-sm transition-colors"
              >
                {t.common.cancel}
              </button>
              <button
                onClick={handleOpenSkillFolder}
                className="bg-primary text-primary-foreground hover:bg-primary/90 flex h-9 items-center gap-2 rounded-lg px-4 text-sm font-medium transition-colors"
              >
                <FolderOpen className="size-4" />
                {t.settings.skillsOpenFolder}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Import from GitHub Dialog */}
      {showGitHubImport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div
            className="fixed inset-0 bg-black/50"
            onClick={() => {
              setShowGitHubImport(false);
              setGithubUrl('');
              setGithubBranch('');
              setGithubPath('');
              setImportError('');
            }}
          />
          <div className="bg-background border-border relative z-10 w-[420px] rounded-xl border p-6 shadow-lg">
            <button
              onClick={() => {
                setShowGitHubImport(false);
                setGithubUrl('');
                setGithubBranch('');
                setGithubPath('');
                setImportError('');
              }}
              className="text-muted-foreground hover:text-foreground absolute top-4 right-4"
            >
              <X className="size-5" />
            </button>

            {/* Icons */}
            <div className="mb-4 flex items-center justify-center gap-3">
              <div className="bg-muted flex size-12 items-center justify-center rounded-xl">
                <Github className="size-6" />
              </div>
              <ArrowLeftRight className="text-muted-foreground size-5" />
              <div className="bg-muted flex size-12 items-center justify-center rounded-xl">
                <Layers className="size-6" />
              </div>
            </div>

            <h3 className="text-foreground mb-2 text-center text-lg font-semibold">
              {t.settings.skillsImportGitHub}
            </h3>
            <p className="text-muted-foreground mb-6 text-center text-sm">
              {t.settings.skillsImportGitHubDialogDesc}
            </p>

            <div className="mb-4">
              <label className="text-foreground mb-2 block text-sm font-medium">
                URL
              </label>
              <input
                type="text"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                placeholder="https://github.com/username/repo"
                className="border-input bg-muted text-foreground placeholder:text-muted-foreground focus:ring-ring h-11 w-full rounded-lg border px-3 text-sm focus:ring-2 focus:outline-none"
              />
            </div>

            <div className="mb-4 grid grid-cols-2 gap-3">
              <div>
                <label className="text-foreground mb-2 block text-sm font-medium">
                  {t.settings.skillsImportBranch}
                </label>
                <input
                  type="text"
                  value={githubBranch}
                  onChange={(e) => setGithubBranch(e.target.value)}
                  placeholder={t.settings.skillsImportBranchPlaceholder}
                  className="border-input bg-muted text-foreground placeholder:text-muted-foreground focus:ring-ring h-11 w-full rounded-lg border px-3 text-sm focus:ring-2 focus:outline-none"
                />
              </div>
              <div>
                <label className="text-foreground mb-2 block text-sm font-medium">
                  {t.settings.skillsImportPath}
                </label>
                <input
                  type="text"
                  value={githubPath}
                  onChange={(e) => setGithubPath(e.target.value)}
                  placeholder={t.settings.skillsImportPathPlaceholder}
                  className="border-input bg-muted text-foreground placeholder:text-muted-foreground focus:ring-ring h-11 w-full rounded-lg border px-3 text-sm focus:ring-2 focus:outline-none"
                />
              </div>
            </div>

            {importError && (
              <div className="mb-4 rounded-lg border border-red-400/50 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                {importError}
              </div>
            )}

            <button
              onClick={async () => {
                if (!githubUrl) return;
                const targetDir = skillsDirs.user || defaultSkillsPath;
                if (!targetDir) {
                  setImportError(t.settings.skillsImportTargetUnavailable);
                  return;
                }
                setImporting(true);
                setImportError('');
                const trimmedBranch = githubBranch.trim();
                const trimmedPath = githubPath.trim();
                try {
                  const response = await fetch(
                    `${API_BASE_URL}/files/import-skill`,
                    {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        url: githubUrl,
                        targetDir,
                        ...(trimmedBranch ? { branch: trimmedBranch } : {}),
                        ...(trimmedPath ? { path: trimmedPath } : {}),
                      }),
                    }
                  );
                  const data = await response.json();
                  if (data.success) {
                    setShowGitHubImport(false);
                    setGithubUrl('');
                    setGithubBranch('');
                    setGithubPath('');
                    // Reload skills
                    loadSkillsFromPath(settings.skillsPath || '');
                  } else {
                    const message = String(
                      data.error || t.settings.skillsImportFailed
                    );
                    setImportError(message);
                    console.error('[Skills] Import failed:', message);
                  }
                } catch (err) {
                  const message =
                    err instanceof Error
                      ? err.message
                      : t.settings.skillsImportFailed;
                  setImportError(message);
                  console.error('[Skills] Import error:', err);
                } finally {
                  setImporting(false);
                }
              }}
              disabled={!githubUrl || importing}
              className="bg-foreground text-background hover:bg-foreground/90 flex h-11 w-full items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            >
              {importing ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  {t.settings.skillsImporting}
                </>
              ) : (
                t.settings.skillsImport
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
