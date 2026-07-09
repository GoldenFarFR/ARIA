import { AcpAgent, PrivyAlchemyEvmProviderAdapter } from \"@virtuals-protocol/acp-node-v2\";
import { baseSepolia } from \"@account-kit/infra\";

async function main() {
  console.log(\"🚀 Connexion à Aria Vanguard ZHC...\");

  const agent = await AcpAgent.create({
    provider: await PrivyAlchemyEvmProviderAdapter.create({
      // Jamais de cle privee en dur dans le repo -- toujours depuis l'environnement local,
      // jamais commitee (cf. "cle privee jamais sur le serveur", CLAUDE.md).
      walletAddress: process.env.ACP_WALLET_ADDRESS,
      chains: [baseSepolia],
      signerPrivateKey: process.env.ACP_SIGNER_PRIVATE_KEY,
    }),
    builderCode: process.env.ACP_BUILDER_CODE,
  });

  console.log(\"✅ Agent connecté avec succès !\");
  console.log(\"Wallet Address :\", await agent.getAddress());
}

main().catch(err => console.error(\"❌ Erreur :\", err.message));
