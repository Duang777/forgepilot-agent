/**
 * Sandbox Provider Types
 *
 * Defines the interfaces for extensible sandbox providers.
 * Supports: Codex (Process), Claude (Container), Native (Process), Docker, E2B
 *
 * The provider type is now a string to support custom extensions.
 * Built-in types: "codex", "claude", "native", "docker", "e2b"
 */

// ============================================================================
// Provider Types
// ============================================================================

/**
 * Built-in sandbox provider types
 */
export type BuiltinSandboxProviderType =
  | 'docker'
  | 'native'
  | 'e2b'
  | 'codex'
  | 'claude';

/**
 * Sandbox provider type - string to allow custom extensions
 * Built-in types: "codex", "claude", "native", "docker", "e2b"
 */
export type SandboxProviderType = BuiltinSandboxProviderType | (string & {});

export interface SandboxCapabilities {
  /** Whether volume mounts from host are supported */
  supportsVolumeMounts: boolean;
  /** Whether network access is available */
  supportsNetworking: boolean;
  /** Level of isolation provided */
  isolation: 'vm' | 'container' | 'process' | 'none';
  /** Supported runtime environments */
  supportedRuntimes: string[]; // ["node", "python", "bun"]
  /** Whether the provider supports multiple concurrent instances */
  supportsPooling: boolean;
}

// ============================================================================
// Execution Options and Results
// ============================================================================

export interface SandboxExecOptions {
  /** Command to execute */
  command: string;
  /** Command arguments */
  args?: string[];
  /** Working directory inside sandbox */
  cwd?: string;
  /** Environment variables */
  env?: Record<string, string>;
  /** Timeout in milliseconds */
  timeout?: number;
  /** Container/VM image to use (provider-specific) */
  image?: string;
}

export interface SandboxExecResult {
  /** Standard output */
  stdout: string;
  /** Standard error */
  stderr: string;
  /** Exit code */
  exitCode: number;
  /** Execution duration in milliseconds */
  duration: number;
  /** Provider that executed the command (for UI display) */
  provider?: {
    type: SandboxProviderType;
    name: string;
    isolation: 'vm' | 'container' | 'process' | 'none';
  };
}

export interface ScriptOptions {
  /** Script arguments */
  args?: string[];
  /** Environment variables */
  env?: Record<string, string>;
  /** Timeout in milliseconds */
  timeout?: number;
  /** Additional packages to install before running */
  packages?: string[];
}

// ============================================================================
// Volume Mounts
// ============================================================================

export interface VolumeMount {
  /** Path on the host system */
  hostPath: string;
  /** Path inside the sandbox */
  guestPath: string;
  /** Whether the mount is read-only */
  readOnly?: boolean;
}

// ============================================================================
// Provider Configuration
// ============================================================================

export interface SandboxProviderConfig {
  /** Provider type identifier */
  type: SandboxProviderType;
  /** Human-readable name */
  name: string;
  /** Whether this provider is enabled */
  enabled: boolean;
  /** Provider-specific configuration */
  config: Record<string, unknown>;
}

export interface DockerProviderConfig extends SandboxProviderConfig {
  type: 'docker';
  config: {
    /** Docker socket path */
    socketPath?: string;
    /** Default container image */
    defaultImage?: string;
    /** Memory limit (e.g., "1g") */
    memoryLimit?: string;
    /** CPU limit (e.g., "1.0") */
    cpuLimit?: string;
  };
}

export interface NativeProviderConfig extends SandboxProviderConfig {
  type: 'native';
  config: {
    /** Allowed directories for execution */
    allowedDirectories?: string[];
    /** Shell to use for commands */
    shell?: string;
    /** Default timeout in milliseconds */
    defaultTimeout?: number;
  };
}

export interface E2BProviderConfig extends SandboxProviderConfig {
  type: 'e2b';
  config: {
    /** E2B API key */
    apiKey?: string;
    /** Sandbox template ID */
    templateId?: string;
    /** Sandbox timeout */
    timeout?: number;
  };
}

// ============================================================================
// Provider Interface
// ============================================================================

/**
 * Base interface for all sandbox providers.
 * Each provider (Codex, Claude, Docker, Native, etc.) must implement this interface.
 */
export interface ISandboxProvider {
  /** Provider type identifier */
  readonly type: SandboxProviderType;
  /** Human-readable provider name */
  readonly name: string;

  /**
   * Check if this provider is available on the current platform
   */
  isAvailable(): Promise<boolean>;

  /**
   * Initialize the provider with optional configuration
   */
  init(config?: Record<string, unknown>): Promise<void>;

  /**
   * Execute a command in the sandbox
   */
  exec(options: SandboxExecOptions): Promise<SandboxExecResult>;

  /**
   * Run a script file in the sandbox
   */
  runScript(
    filePath: string,
    workDir: string,
    options?: ScriptOptions
  ): Promise<SandboxExecResult>;

  /**
   * Stop and cleanup the provider
   */
  stop(): Promise<void>;

  /**
   * Shutdown the provider (alias for stop)
   */
  shutdown(): Promise<void>;

  /**
   * Get the capabilities of this provider
   */
  getCapabilities(): SandboxCapabilities;

  /**
   * Set volume mounts (for providers that support it)
   */
  setVolumes?(volumes: VolumeMount[]): void;
}

// ============================================================================
// Factory Types
// ============================================================================

export type SandboxProviderFactory = (
  config?: SandboxProviderConfig
) => ISandboxProvider;

export interface SandboxProviderRegistry {
  register(type: SandboxProviderType, factory: SandboxProviderFactory): void;
  get(type: SandboxProviderType): SandboxProviderFactory | undefined;
  create(config: SandboxProviderConfig): ISandboxProvider;
  getAvailable(): Promise<SandboxProviderType[]>;
}

// ============================================================================
// Default Images
// ============================================================================

export const SANDBOX_IMAGES = {
  node: 'node:18-alpine',
  python: 'python:3.11-slim',
  bun: 'oven/bun:latest',
} as const;

export type SandboxImage = keyof typeof SANDBOX_IMAGES;

export interface SandboxConfig {
  /** Whether sandbox mode is enabled */
  enabled: boolean;
  /** Sandbox provider to use */
  provider?: SandboxProviderType;
  /** Container image to use */
  image?: string;
  /** API endpoint for sandbox service (deprecated, use provider instead) */
  apiEndpoint?: string;
  /** Provider-specific configuration */
  providerConfig?: Record<string, unknown>;
}
