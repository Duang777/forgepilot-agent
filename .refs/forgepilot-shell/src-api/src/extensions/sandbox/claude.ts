/**
 * Claude Sandbox Provider
 *
 * Uses Anthropic's official sandbox-runtime (@anthropic-ai/sandbox-runtime) for isolated code execution.
 * The sandbox runtime provides a secure environment for running scripts.
 */

import { execSync, spawn } from 'child_process';
import { existsSync } from 'fs';
import { homedir, platform } from 'os';
import * as path from 'path';

import { defineSandboxPlugin } from '@/core/sandbox/plugin';
import type {
  SandboxPlugin,
  SandboxProviderMetadata,
} from '@/core/sandbox/plugin';
import type {
  ISandboxProvider,
  SandboxCapabilities,
  SandboxExecOptions,
  SandboxExecResult,
  SandboxProviderType,
  ScriptOptions,
  VolumeMount,
} from '@/core/sandbox/types';

/**
 * Get the path to the srt executable
 */
function getSrtPath(): string | undefined {
  const os = platform();

  try {
    if (os === 'win32') {
      const whereResult = execSync('where srt', {
        encoding: 'utf-8',
        stdio: 'pipe',
      }).trim();
      const firstPath = whereResult.split('\n')[0];
      if (firstPath && existsSync(firstPath)) {
        return firstPath;
      }
    } else {
      const whichResult = execSync('which srt', {
        encoding: 'utf-8',
        stdio: 'pipe',
      }).trim();
      if (whichResult && existsSync(whichResult)) {
        return whichResult;
      }
    }
  } catch {
    // Not found via which/where
  }

  // Check common install locations
  const commonPaths =
    os === 'win32'
      ? [path.join(homedir(), 'AppData', 'Roaming', 'npm', 'srt.cmd')]
      : [
          '/usr/local/bin/srt',
          path.join(homedir(), '.local', 'bin', 'srt'),
          path.join(homedir(), '.npm-global', 'bin', 'srt'),
        ];

  for (const p of commonPaths) {
    if (existsSync(p)) {
      return p;
    }
  }

  // Check SRT_PATH env var
  if (process.env.SRT_PATH && existsSync(process.env.SRT_PATH)) {
    return process.env.SRT_PATH;
  }

  return undefined;
}

export class ClaudeProvider implements ISandboxProvider {
  readonly type: SandboxProviderType = 'claude';
  readonly name = 'Claude Sandbox';
  readonly version = '1.0.0';

  private srtPath: string | undefined;
  private volumes: VolumeMount[] = [];

  async isAvailable(): Promise<boolean> {
    this.srtPath = getSrtPath();
    return this.srtPath !== undefined;
  }

  async init(_config?: Record<string, unknown>): Promise<void> {
    this.srtPath = getSrtPath();
    if (!this.srtPath) {
      console.warn(
        '[ClaudeProvider] Sandbox Runtime not found. Install with: npm install -g @anthropic-ai/sandbox-runtime'
      );
    } else {
      console.log(`[ClaudeProvider] Using Sandbox Runtime at: ${this.srtPath}`);
    }
  }

  async exec(options: SandboxExecOptions): Promise<SandboxExecResult> {
    const startTime = Date.now();
    const { command, args = [], cwd, env, timeout } = options;

    if (!this.srtPath) {
      return {
        stdout: '',
        stderr: 'Sandbox Runtime is not installed',
        exitCode: 1,
        duration: Date.now() - startTime,
      };
    }

    const workDir = cwd || process.cwd();

    return new Promise((resolve) => {
      // Check if command contains shell operators (&&, ||, |, ;, >, <) or has arguments embedded (space)
      const needsShell =
        /[&|;<>]/.test(command) || (command.includes(' ') && args.length === 0);

      console.log(`[ClaudeProvider] Sandbox exec workDir: ${workDir}`);

      let spawnArgs: string[];
      if (needsShell) {
        // Wrap command in sh -c for shell interpretation
        const fullCommand =
          args.length > 0 ? `${command} ${args.join(' ')}` : command;
        spawnArgs = ['run', '--', 'sh', '-c', fullCommand];
      } else {
        spawnArgs = ['run', '--', command, ...args];
      }

      const proc = spawn(this.srtPath!, spawnArgs, {
        cwd: workDir,
        env: { ...process.env, ...env },
        stdio: ['ignore', 'pipe', 'pipe'],
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr?.on('data', (data) => {
        stderr += data.toString();
      });

      const timeoutId = timeout
        ? setTimeout(() => {
            proc.kill('SIGTERM');
            stderr += '\nExecution timed out';
          }, timeout)
        : undefined;

      proc.on('close', (code) => {
        if (timeoutId) clearTimeout(timeoutId);
        resolve({
          stdout,
          stderr,
          exitCode: code || 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on('error', (error) => {
        if (timeoutId) clearTimeout(timeoutId);
        resolve({
          stdout,
          stderr: stderr + '\n' + error.message,
          exitCode: 1,
          duration: Date.now() - startTime,
        });
      });
    });
  }

  async runScript(
    filePath: string,
    workDir: string,
    options?: ScriptOptions
  ): Promise<SandboxExecResult> {
    const startTime = Date.now();
    const ext = path.extname(filePath).toLowerCase();

    if (!this.srtPath) {
      return {
        stdout: '',
        stderr:
          'Sandbox Runtime is not installed. Install with: npm install -g @anthropic-ai/sandbox-runtime',
        exitCode: 1,
        duration: Date.now() - startTime,
      };
    }

    // Detect runtime
    let runtime = 'python';
    let runtimeArgs: string[] = [filePath];
    let isPython = true;

    if (ext === '.js' || ext === '.mjs') {
      runtime = 'node';
      isPython = false;
    } else if (ext === '.ts' || ext === '.mts') {
      runtime = 'npx';
      runtimeArgs = ['tsx', filePath];
      isPython = false;
    }

    // Add script args
    if (options?.args) {
      runtimeArgs.push(...options.args);
    }

    console.log(`[ClaudeProvider] Running script: ${filePath}`);
    console.log(
      `[ClaudeProvider] Runtime: ${runtime}, Args: ${runtimeArgs.join(' ')}`
    );

    // Install packages OUTSIDE the sandbox first
    if (options?.packages && options.packages.length > 0) {
      console.log(
        `[ClaudeProvider] Installing packages: ${options.packages.join(', ')}`
      );
      try {
        if (isPython) {
          // Use pip to install Python packages
          const pipCmd = `pip install ${options.packages.join(' ')}`;
          console.log(`[ClaudeProvider] Running: ${pipCmd}`);
          execSync(pipCmd, {
            cwd: workDir,
            encoding: 'utf-8',
            stdio: 'pipe',
            timeout: 60000, // 60 second timeout for package installation
          });
        } else {
          // Use npm to install Node.js packages
          const npmCmd = `npm install ${options.packages.join(' ')}`;
          console.log(`[ClaudeProvider] Running: ${npmCmd}`);
          execSync(npmCmd, {
            cwd: workDir,
            encoding: 'utf-8',
            stdio: 'pipe',
            timeout: 60000,
          });
        }
        console.log(`[ClaudeProvider] Packages installed successfully`);
      } catch (error) {
        const errMsg = error instanceof Error ? error.message : String(error);
        console.error(`[ClaudeProvider] Failed to install packages: ${errMsg}`);
        return {
          stdout: '',
          stderr: `Failed to install packages: ${errMsg}`,
          exitCode: 1,
          duration: Date.now() - startTime,
        };
      }
    }

    return new Promise((resolve) => {
      console.log(`[ClaudeProvider] Sandbox workDir: ${workDir}`);

      // Use srt run command to execute in sandbox
      const proc = spawn(
        this.srtPath!,
        ['run', '--', runtime, ...runtimeArgs],
        {
          cwd: workDir,
          env: { ...process.env, ...options?.env },
          stdio: ['ignore', 'pipe', 'pipe'],
        }
      );

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr?.on('data', (data) => {
        stderr += data.toString();
      });

      const timeout = options?.timeout || 120000;
      const timeoutId = setTimeout(() => {
        proc.kill('SIGTERM');
        stderr += '\nExecution timed out';
      }, timeout);

      proc.on('close', (code) => {
        clearTimeout(timeoutId);
        resolve({
          stdout,
          stderr,
          exitCode: code || 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on('error', (error) => {
        clearTimeout(timeoutId);
        resolve({
          stdout,
          stderr: stderr + '\n' + error.message,
          exitCode: 1,
          duration: Date.now() - startTime,
        });
      });
    });
  }

  async stop(): Promise<void> {
    // No persistent state to clean up
  }

  async shutdown(): Promise<void> {
    return this.stop();
  }

  getCapabilities(): SandboxCapabilities {
    return {
      supportsVolumeMounts: false,
      supportsNetworking: true, // Claude sandbox allows network access
      isolation: 'process', // Uses OS-level sandboxing
      supportedRuntimes: ['node', 'python', 'bun'],
      supportsPooling: false,
    };
  }

  setVolumes(volumes: VolumeMount[]): void {
    this.volumes = volumes;
  }
}

/**
 * Metadata for Claude sandbox provider
 */
export const CLAUDE_SANDBOX_METADATA: SandboxProviderMetadata = {
  type: 'claude',
  name: 'Claude Sandbox',
  version: '1.0.0',
  description:
    "Uses Anthropic's sandbox runtime for isolated script execution.",
  configSchema: {
    type: 'object',
    properties: {
      srtPath: {
        type: 'string',
        description:
          'Path to the srt executable (auto-detected if not provided)',
      },
    },
  },
  isolation: 'process',
  supportedRuntimes: ['node', 'python', 'bun'],
  supportsVolumeMounts: false,
  supportsNetworking: true,
  supportsPooling: false,
};

/**
 * Factory function for ClaudeProvider
 */
export function createClaudeProvider(): ClaudeProvider {
  return new ClaudeProvider();
}

/**
 * Claude sandbox provider plugin definition
 */
export const claudePlugin: SandboxPlugin = defineSandboxPlugin({
  metadata: CLAUDE_SANDBOX_METADATA,
  factory: () => createClaudeProvider(),
});
