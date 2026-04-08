/**
 * Native Sandbox Provider
 *
 * Executes commands directly on the host system without isolation.
 * This is a fallback provider when no VM/container solution is available.
 *
 * WARNING: No security isolation is provided.
 */

import { spawn } from 'child_process';
import * as path from 'path';

import { defineSandboxPlugin, NATIVE_METADATA } from '@/core/sandbox/plugin';
import type { SandboxPlugin } from '@/core/sandbox/plugin';
import type {
  ISandboxProvider,
  NativeProviderConfig,
  SandboxCapabilities,
  SandboxExecOptions,
  SandboxExecResult,
  SandboxProviderType,
  ScriptOptions,
  VolumeMount,
} from '@/core/sandbox/types';

export class NativeProvider implements ISandboxProvider {
  readonly type: SandboxProviderType = 'native';
  readonly name = 'Native (No Isolation)';
  readonly version = '1.0.0';

  private config: NativeProviderConfig['config'] = {
    allowedDirectories: [],
    shell: process.platform === 'win32' ? 'cmd.exe' : '/bin/bash',
    defaultTimeout: 120000,
  };

  private volumes: VolumeMount[] = [];

  async isAvailable(): Promise<boolean> {
    // Native execution is always available
    return true;
  }

  async init(config?: Record<string, unknown>): Promise<void> {
    if (config) {
      this.config = { ...this.config, ...config };
    }
    console.log('[NativeProvider] Initialized (no isolation)');
  }

  async exec(options: SandboxExecOptions): Promise<SandboxExecResult> {
    const startTime = Date.now();
    const { command, args = [], cwd, env, timeout } = options;

    const workDir = cwd || process.cwd();
    const execTimeout = timeout || this.config.defaultTimeout || 120000;

    return new Promise((resolve) => {
      console.log(
        `[NativeProvider] Executing: ${command} ${args.join(' ')} (cwd: ${workDir})`
      );

      const proc = spawn(command, args, {
        cwd: workDir,
        env: { ...process.env, ...env },
        shell: true,
        timeout: execTimeout,
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data) => {
        stdout += data.toString();
      });

      proc.stderr?.on('data', (data) => {
        stderr += data.toString();
      });

      proc.on('close', (code) => {
        resolve({
          stdout,
          stderr,
          exitCode: code || 0,
          duration: Date.now() - startTime,
        });
      });

      proc.on('error', (error) => {
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
    const ext = path.extname(filePath).toLowerCase();
    let runtime = 'node';
    let runtimeArgs: string[] = [];

    switch (ext) {
      case '.py':
        runtime = 'python3';
        break;
      case '.ts':
      case '.mts':
        runtime = 'npx';
        runtimeArgs = ['tsx'];
        break;
      case '.js':
      case '.mjs':
        runtime = 'node';
        break;
      case '.sh':
        runtime = 'bash';
        break;
      default:
        runtime = 'node';
    }

    // Install packages if specified
    if (options?.packages && options.packages.length > 0 && ext !== '.py') {
      console.log(
        `[NativeProvider] Installing packages: ${options.packages.join(', ')}`
      );
      await this.exec({
        command: 'npm',
        args: ['install', '--no-save', ...options.packages],
        cwd: workDir,
        env: options.env,
      });
    }

    return this.exec({
      command: runtime,
      args: [...runtimeArgs, filePath, ...(options?.args || [])],
      cwd: workDir,
      env: options?.env,
      timeout: options?.timeout,
    });
  }

  async stop(): Promise<void> {
    console.log('[NativeProvider] Stopped');
  }

  async shutdown(): Promise<void> {
    return this.stop();
  }

  getCapabilities(): SandboxCapabilities {
    return {
      supportsVolumeMounts: false, // N/A - runs on host directly
      supportsNetworking: true,
      isolation: 'none',
      supportedRuntimes: ['node', 'python', 'bun', 'bash'],
      supportsPooling: false,
    };
  }

  setVolumes(volumes: VolumeMount[]): void {
    // Native provider doesn't need volume mounts since it runs on host
    this.volumes = volumes;
  }
}

/**
 * Factory function for NativeProvider
 */
export function createNativeProvider(config?: {
  config?: NativeProviderConfig['config'];
}): NativeProvider {
  const provider = new NativeProvider();
  if (config?.config) {
    provider.init(config.config);
  }
  return provider;
}

/**
 * Native provider plugin definition
 */
export const nativePlugin: SandboxPlugin = defineSandboxPlugin({
  metadata: NATIVE_METADATA,
  factory: (config) =>
    createNativeProvider(config ? { config: config.config } : undefined),
});
