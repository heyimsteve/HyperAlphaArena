/**
 * Hyperliquid One-Click Wallet Setup
 *
 * Handles browser wallet connection via EIP-1193, EIP-712 typed data signing
 * for ApproveBuilderFee and ApproveAgent actions, and submission to Hyperliquid API.
 */

import { ethers } from 'ethers'

// Error codes for i18n translation in UI components
export const WALLET_ERROR = {
  NO_WALLET: 'NO_BROWSER_WALLET',
  NO_ACCOUNT: 'NO_ACCOUNT_SELECTED',
  NOT_DEPOSITED: 'NOT_DEPOSITED',
  USER_REJECTED: 'USER_REJECTED',
  SETUP_FAILED: 'SETUP_FAILED',
} as const

class WalletSetupError extends Error {
  errorCode: string
  env?: string
  constructor(code: string, message: string, env?: string) {
    super(message)
    this.errorCode = code
    this.env = env
  }
}

// Hyperliquid EIP-712 domain (same for all user-signed actions)
const EIP712_DOMAIN = {
  name: 'HyperliquidSignTransaction',
  version: '1',
  chainId: 0x66eee,
  verifyingContract: '0x0000000000000000000000000000000000000000',
}

// EIP712Domain type definition — must be explicit to match SDK's encode_typed_data
const EIP712_DOMAIN_TYPE = [
  { name: 'name', type: 'string' },
  { name: 'version', type: 'string' },
  { name: 'chainId', type: 'uint256' },
  { name: 'verifyingContract', type: 'address' },
]

// Builder fee config
const BUILDER_ADDRESS = '0x950D50d2e1C009212B1511e1Ec06F572d577AC15'
const BUILDER_FEE_RATE = '0.03%' // 30 internal units = 3 bps = 0.03%
const BUILDER_FEE_THRESHOLD = 30

const MAINNET_API = 'https://api.hyperliquid.xyz'
const TESTNET_API = 'https://api.hyperliquid-testnet.xyz'

// Hyperliquid requires signing on this chainId (Arbitrum Sepolia)
const SIGNING_CHAIN_ID = '0x66eee'

function getApiUrl(env: 'testnet' | 'mainnet') {
  return env === 'mainnet' ? MAINNET_API : TESTNET_API
}

function getChainName(env: 'testnet' | 'mainnet') {
  return env === 'mainnet' ? 'Mainnet' : 'Testnet'
}

/**
 * Connect to browser wallet via EIP-1193 (MetaMask, Rabby, OKX, Coinbase, etc.)
 */
export async function connectBrowserWallet(): Promise<string> {
  const ethereum = (window as any).ethereum
  if (!ethereum) {
    throw new WalletSetupError(WALLET_ERROR.NO_WALLET, 'No browser wallet detected')
  }
  const accounts: string[] = await ethereum.request({ method: 'eth_requestAccounts' })
  if (!accounts || accounts.length === 0) {
    throw new WalletSetupError(WALLET_ERROR.NO_ACCOUNT, 'No account selected in wallet')
  }
  return accounts[0]
}

/**
 * Check if an error is a chainId mismatch from the wallet.
 * Different wallets use different error messages/codes for this.
 */
function isChainIdMismatchError(err: any): boolean {
  const msg = (err?.message || '').toLowerCase()
  return msg.includes('chainid') && (msg.includes('mismatch') || msg.includes('must match') || msg.includes('should be same') || msg.includes('same as current'))
}

/**
 * Switch wallet to signing chain (0x66eee / Arbitrum Sepolia).
 * Tries switch first, then add+switch if chain unknown.
 */
async function switchToSigningChain(): Promise<void> {
  const ethereum = (window as any).ethereum
  try {
    await ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: SIGNING_CHAIN_ID }],
    })
  } catch (err: any) {
    if (err?.code === 4001) throw err
    // Chain unknown — try adding it, then switch
    try {
      await ethereum.request({
        method: 'wallet_addEthereumChain',
        params: [{
          chainId: SIGNING_CHAIN_ID,
          chainName: 'Arbitrum Sepolia',
          rpcUrls: ['https://sepolia-rollup.arbitrum.io/rpc'],
          nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
          blockExplorerUrls: ['https://sepolia.arbiscan.io'],
        }],
      })
    } catch (addErr: any) {
      if (addErr?.code === 4001) throw addErr
      throw new Error('Failed to add signing chain to wallet')
    }
  }
}

/**
 * Restore wallet to its previous chain after signing is complete.
 */
async function restoreChain(previousChainId: string | null): Promise<void> {
  if (!previousChainId) return
  const ethereum = (window as any).ethereum
  try {
    await ethereum.request({
      method: 'wallet_switchEthereumChain',
      params: [{ chainId: previousChainId }],
    })
  } catch {
    // Best-effort restore
  }
}

/**
 * Sign EIP-712 typed data with automatic chainId mismatch fallback.
 *
 * Strategy (per best practices):
 * 1. Try signing directly — works for Rabby, OKX, and wallets that don't enforce chainId
 * 2. If chainId mismatch error (MetaMask, etc.) — switch chain, then retry
 * 3. User rejection (4001) always aborts immediately
 */
async function signTypedData(address: string, typedData: any): Promise<string> {
  const ethereum = (window as any).ethereum
  const payload = JSON.stringify(typedData)

  try {
    // Attempt 1: sign directly (Rabby/OKX will succeed here)
    return await ethereum.request({
      method: 'eth_signTypedData_v4',
      params: [address, payload],
    })
  } catch (err: any) {
    if (err?.code === 4001) throw err

    if (isChainIdMismatchError(err)) {
      // MetaMask-style wallet: needs chain switch first
      console.warn('[WalletSetup] chainId mismatch, switching chain and retrying...')
      await switchToSigningChain()
      // Attempt 2: sign after chain switch
      return await ethereum.request({
        method: 'eth_signTypedData_v4',
        params: [address, payload],
      })
    }

    throw err
  }
}

/**
 * Parse EIP-712 signature hex into {r, s, v} format for Hyperliquid API
 */
function parseSignature(sigHex: string): { r: string; s: string; v: number } {
  const sig = ethers.Signature.from(sigHex)
  return { r: sig.r, s: sig.s, v: sig.v }
}

/**
 * Convert Hyperliquid API errors to user-friendly messages
 */
function throwFriendlyError(response: string, env: 'testnet' | 'mainnet'): never {
  if (response?.includes('Must deposit before performing actions')) {
    throw new WalletSetupError(WALLET_ERROR.NOT_DEPOSITED, response, env)
  }
  throw new WalletSetupError(WALLET_ERROR.SETUP_FAILED, response || 'Unknown error', env)
}

/**
 * Safe JSON parse from fetch response with proper error handling
 */
async function parseJsonResponse(resp: Response, context: string): Promise<any> {
  const text = await resp.text()
  if (!resp.ok) {
    console.error(`[WalletSetup] ${context} HTTP ${resp.status}: ${text}`)
    throw new Error(`${context}: ${text}`)
  }
  try {
    return JSON.parse(text)
  } catch {
    console.error(`[WalletSetup] ${context} invalid JSON response: ${text}`)
    throw new Error(`${context}: unexpected response from server`)
  }
}

/**
 * Build EIP-712 typed data matching the SDK's user_signed_payload format exactly.
 * - Includes EIP712Domain in types (matches SDK's encode_typed_data)
 * - Message contains ONLY the fields defined in the primary type
 */
function buildTypedData(
  primaryType: string,
  primaryTypeFields: Array<{ name: string; type: string }>,
  messageValues: Record<string, any>,
) {
  // Build clean message with only the typed fields
  const message: Record<string, any> = {}
  for (const field of primaryTypeFields) {
    message[field.name] = messageValues[field.name]
  }

  return {
    domain: EIP712_DOMAIN,
    types: {
      EIP712Domain: EIP712_DOMAIN_TYPE,
      [primaryType]: primaryTypeFields,
    },
    primaryType,
    message,
  }
}

// Type definitions matching the Hyperliquid SDK
const APPROVE_BUILDER_FEE_TYPES = [
  { name: 'hyperliquidChain', type: 'string' },
  { name: 'maxFeeRate', type: 'string' },
  { name: 'builder', type: 'address' },
  { name: 'nonce', type: 'uint64' },
]

const APPROVE_AGENT_TYPES = [
  { name: 'hyperliquidChain', type: 'string' },
  { name: 'agentAddress', type: 'address' },
  { name: 'agentName', type: 'string' },
  { name: 'nonce', type: 'uint64' },
]

/**
 * Check if builder fee is already authorized for a wallet address
 */
export async function checkBuilderFeeAuthorized(
  masterAddress: string,
  env: 'testnet' | 'mainnet'
): Promise<boolean> {
  if (env === 'testnet') return true
  try {
    const resp = await fetch(`${MAINNET_API}/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: 'maxBuilderFee',
        user: masterAddress,
        builder: BUILDER_ADDRESS,
      }),
    })
    const maxFee = await parseJsonResponse(resp, 'Check builder fee')
    return maxFee >= BUILDER_FEE_THRESHOLD
  } catch {
    return false
  }
}

/**
 * Sign and submit ApproveBuilderFee action via browser wallet
 */
export async function approveBuilderFee(
  masterAddress: string,
  env: 'testnet' | 'mainnet'
): Promise<void> {
  const nonce = Date.now()

  const messageValues = {
    hyperliquidChain: getChainName(env),
    maxFeeRate: BUILDER_FEE_RATE,
    builder: BUILDER_ADDRESS,
    nonce,
  }

  const typedData = buildTypedData(
    'HyperliquidTransaction:ApproveBuilderFee',
    APPROVE_BUILDER_FEE_TYPES,
    messageValues,
  )

  const sigHex = await signTypedData(masterAddress, typedData)
  const signature = parseSignature(sigHex)

  const action = {
    type: 'approveBuilderFee',
    hyperliquidChain: getChainName(env),
    maxFeeRate: BUILDER_FEE_RATE,
    builder: BUILDER_ADDRESS,
    nonce,
    signatureChainId: SIGNING_CHAIN_ID,
  }

  const resp = await fetch(`${getApiUrl(env)}/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, nonce, signature, vaultAddress: null }),
  })

  const result = await parseJsonResponse(resp, 'ApproveBuilderFee')
  if (result.status === 'err') {
    throwFriendlyError(result.response, env)
  }
}

interface AgentSetupResult {
  agentPrivateKey: string
  agentAddress: string
  masterAddress: string
}

/**
 * Generate agent key, sign ApproveAgent via browser wallet, and submit to Hyperliquid
 */
async function createAgentWallet(
  masterAddress: string,
  env: 'testnet' | 'mainnet',
  agentName: string = ''
): Promise<AgentSetupResult> {
  const agentWallet = ethers.Wallet.createRandom()
  const agentPrivateKey = agentWallet.privateKey
  const agentAddress = agentWallet.address

  const nonce = Date.now()

  const messageValues = {
    hyperliquidChain: getChainName(env),
    agentAddress,
    agentName,
    nonce,
  }

  const typedData = buildTypedData(
    'HyperliquidTransaction:ApproveAgent',
    APPROVE_AGENT_TYPES,
    messageValues,
  )

  const sigHex = await signTypedData(masterAddress, typedData)
  const signature = parseSignature(sigHex)

  // Action sent to API includes signatureChainId/hyperliquidChain (SDK convention)
  const action: Record<string, any> = {
    type: 'approveAgent',
    hyperliquidChain: getChainName(env),
    agentAddress,
    agentName,
    nonce,
    signatureChainId: SIGNING_CHAIN_ID,
  }
  if (!agentName) {
    delete action.agentName
  }

  const resp = await fetch(`${getApiUrl(env)}/exchange`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, nonce, signature, vaultAddress: null }),
  })

  const result = await parseJsonResponse(resp, 'ApproveAgent')
  if (result.status === 'err') {
    throwFriendlyError(result.response, env)
  }

  return { agentPrivateKey, agentAddress, masterAddress }
}

export type SetupStep = 'idle' | 'connecting' | 'checking_auth' | 'signing_auth' | 'signing_agent' | 'saving' | 'done' | 'error'

export interface SetupProgress {
  step: SetupStep
  message: string
  error?: string
}

/**
 * Full one-click wallet setup flow:
 * 1. Connect browser wallet
 * 2. (Mainnet only) Check & sign builder fee authorization
 * 3. Generate agent key & sign ApproveAgent
 * 4. Save to backend via configureAgentWallet API
 *
 * Signing strategy: try signing directly first (Rabby/OKX don't need chain switch).
 * If wallet returns chainId mismatch (MetaMask), automatically switch chain and retry.
 */
export async function oneClickWalletSetup(
  env: 'testnet' | 'mainnet',
  accountId: number,
  saveToBackend: (agentKey: string, masterAddr: string) => Promise<void>,
  onProgress: (progress: SetupProgress) => void
): Promise<void> {
  // Track if we switched chains so we can restore later
  const ethereum = (window as any).ethereum
  let originalChainId: string | null = null

  try {
    // Step 1: Connect wallet
    onProgress({ step: 'connecting', message: 'Connecting browser wallet...' })
    await connectBrowserWallet()

    // Record current chain for potential restore later
    originalChainId = await ethereum.request({ method: 'eth_chainId' })

    const accounts: string[] = await ethereum.request({ method: 'eth_accounts' })
    const masterAddress = accounts[0]

    // Step 2: Builder fee check (mainnet only)
    if (env === 'mainnet') {
      onProgress({ step: 'checking_auth', message: 'Checking trading authorization...' })
      const authorized = await checkBuilderFeeAuthorized(masterAddress, env)

      if (!authorized) {
        onProgress({ step: 'signing_auth', message: 'Please approve trading authorization in your wallet...' })
        await approveBuilderFee(masterAddress, env)
      }
    }

    // Step 3: Create agent wallet
    onProgress({ step: 'signing_agent', message: 'Please approve API Wallet creation in your wallet...' })
    const result = await createAgentWallet(masterAddress, env, 'HyperArena')

    // Restore wallet to original chain if it was changed
    const currentChain = await ethereum.request({ method: 'eth_chainId' })
    if (originalChainId && currentChain.toLowerCase() !== originalChainId.toLowerCase()) {
      await restoreChain(originalChainId)
    }

    // Step 4: Save to backend
    onProgress({ step: 'saving', message: 'Saving wallet configuration...' })
    await saveToBackend(result.agentPrivateKey, result.masterAddress)

    onProgress({ step: 'done', message: 'Wallet setup complete!' })
  } catch (err: any) {
    // Restore chain on error
    try {
      const currentChain = await ethereum.request({ method: 'eth_chainId' })
      if (originalChainId && currentChain.toLowerCase() !== originalChainId.toLowerCase()) {
        await restoreChain(originalChainId)
      }
    } catch { /* best effort */ }

    if (err?.code === 4001) {
      err.errorCode = WALLET_ERROR.USER_REJECTED
    }
    onProgress({ step: 'error', message: err?.message || 'Setup failed', error: err?.errorCode || '' })
    throw err
  }
}
