import { AcpAgent, PrivyAlchemyEvmProviderAdapter } from \"@virtuals-protocol/acp-node-v2\";
import { baseSepolia } from \"@account-kit/infra\";

async function main() {
  console.log(\"🚀 Connexion à Aria Vanguard ZHC...\");

  const agent = await AcpAgent.create({
    provider: await PrivyAlchemyEvmProviderAdapter.create({
      walletAddress: \"0xd752a325433f4d55c5e0b125be84845d7de47bb3\",
      chains: [baseSepolia],
      signerPrivateKey: \"MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgNnkMIkHVZxF1uvFDNazOypimrD31jr4rLLSSQWQC1qahRANCAAROmETNpdP+Lra+oVewzdSIofTcOu3vS85fLNHXuz5wAovNrvoxQH5r+I7P+FTKLk1jm+umqrnV4crzu7Py3rUf",
    }),
    builderCode: \"bc_euy3f9pu\",
  });

  console.log(\"✅ Agent connecté avec succès !\");
  console.log(\"Wallet Address :\", await agent.getAddress());
}

main().catch(err => console.error(\"❌ Erreur :\", err.message));
