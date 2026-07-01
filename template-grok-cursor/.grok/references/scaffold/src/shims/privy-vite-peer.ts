/**
 * Stubs pour le build Vite + Privy (auth email/social uniquement, pas de wallet crypto).
 * Copier vers src/shims/privy-vite-peer.ts dans chaque nouveau site.
 * Source canonique : projets/harmony/src/shims/privy-vite-peer.ts
 */

const fn = (..._args: unknown[]) => {
  throw new Error('Crypto wallet features are disabled on this site.')
}

const ADDR = '11111111111111111111111111111111'

export const pipe = fn
export const createTransactionMessage = fn
export const setTransactionMessageFeePayer = fn
export const setTransactionMessageFeePayerSigner = fn
export const setTransactionMessageLifetimeUsingBlockhash = fn
export const appendTransactionMessageInstructions = fn
export const appendTransactionMessageInstruction = fn
export const partiallySignTransactionMessageWithSigners = fn
export const prependTransactionMessageInstruction = fn
export const getBase64EncodedWireTransaction = fn
export const compileTransaction = fn
export const getTransactionEncoder = fn
export const createSolanaRpc = fn
export const createSolanaRpcSubscriptions = fn
export const devnet = fn
export const mainnet = fn
export const createKeyPairSignerFromBytes = fn
export const createKeyPairSignerFromPrivateKeyBytes = fn
export const isTransactionSigner = fn
export const assertIsTransactionMessageWithBlockhashLifetime = fn
export const decompileTransactionMessageFetchingLookupTables = fn
export const getCompiledTransactionMessageDecoder = fn
export const getSignatureFromTransaction = fn
export const isSolanaError = fn
export const assertIsInstructionWithAccounts = fn
export const assertIsInstructionWithData = fn
export const decompileTransactionMessage = fn
export const fetchEncodedAccounts = fn
export const getBase64Encoder = fn
export const getBase64Decoder = fn
export const getBase58Encoder = fn
export const getBase58Decoder = fn
export const fetchAddressesForLookupTables = fn
export const address = fn
export const getTransactionDecoder = fn
export const isTransactionModifyingSigner = fn
export const isTransactionPartialSigner = fn
export const estimateComputeUnitLimitFactory = fn
export const getSetComputeUnitLimitInstruction = fn
export const setTransactionMessageComputeUnitPrice = fn
export const parseSetComputeUnitLimitInstruction = fn
export const parseSetComputeUnitPriceInstruction = fn
export const fetchMint = fn
export const findAssociatedTokenPda = fn
export const getTransferCheckedInstruction = fn
export const getCreateAssociatedTokenIdempotentInstruction = fn
export const getTransferInstruction = fn
export const identifyToken2022Instruction = fn
export const identifyTokenInstruction = fn
export const parseTransferCheckedInstruction = fn
export const parseTransferCheckedInstruction2022 = fn
export const getTransferSolInstruction = fn
export const createBlockHeightExceedencePromiseFactory = fn
export const waitForRecentTransactionConfirmation = fn
export const createRecentSignatureConfirmationPromiseFactory = fn

export const SOLANA_ERROR__BLOCK_HEIGHT_EXCEEDED = 'BLOCK_HEIGHT_EXCEEDED'
export const TOKEN_PROGRAM_ADDRESS = ADDR
export const TOKEN_2022_PROGRAM_ADDRESS = ADDR
export const COMPUTE_BUDGET_PROGRAM_ADDRESS = ADDR
export const TokenInstruction = {}
export const Token2022Instruction = {}