# Never Play Alone

### A Bittensor subnet for round-based Minecraft agent evaluation

> **Netuid 490 · Bittensor testnet · winner-take-all**

Never Play Alone turns Minecraft into a proving ground for autonomous agents.
Miners upload one `tar.gz` agent package per round to the backend. At round
freeze, validators download the same frozen roster, run every miner against the
same deterministic [mcbench](../neverplayalone_mcbench/) task, upload artifacts
and raw scoreboards, then compute the winner locally using validator stake
weights and set winner-take-all chain weights.

No central referee decides the winner. The chain does.

## Roles at a glance

| Role | What they do | Reward |
| --- | --- | --- |
| **Miner** | Upload one `tar.gz` agent package for the current submission round | Emission if ranked first |
| **Validator** | Download the frozen round roster, run all miners with mcbench, upload scoreboards, compute the winner, set weights | Validator dividends |
| **Owner validator** | A normal validator that can bootstrap the first round and freeze rounds when their evaluation window starts | Same as any validator |

## Architecture

```
        neverplayalone_api                  subtensor (netuid 490)
        ──────────────────                  ──────────────────────
              │                                       │
              │ submission rounds                     │ metagraph / stakes
              │ frozen roster manifests               │ set_weights
              │ validator artifacts + scoreboards     │
              │ consensus result observability        │
              │                                       │
   ┌──────────┴──────────┐                ┌───────────┴────────────┐
   │ owner validator     │                │ other validators       │
   │  - bootstrap round  │                │  - poll current rounds │
   │  - freeze round     │                │  - download roster     │
   │  - run eval too     │                │  - run mcbench batch   │
   └──────────┬──────────┘                │  - upload scoreboards  │
              │                           │  - compute local winner│
              │ frozen roster download    └────────────────────────┘
              ▼
          mcbench batch evaluation
```

## Layout

```
neverplayalone_subnet/
├── cli/        # `npa` CLI for miner submission
├── validator/  # validator binary + backend client
└── README.md
```

## Install

```bash
git clone https://github.com/<this-repo>
cd neverplayalone_subnet
pip install -e .
```

Validators also need `neverplayalone_mcbench` installed and Docker available.
For LLM-based miner agents, validators also need a `CHUTES_API_KEY`.

The backend lives in the separate `neverplayalone_api` repository.

## Be a miner

1. Build a Node-based mcbench-compatible agent and package it as `tar.gz`.
3. Register on netuid 490 testnet.
4. Submit the archive to the backend:

```bash
npa submit ./agent.tar.gz --wallet miner --hotkey hk1
```

Validators will pick it up when the current submission round freezes.

## Run a validator

```bash
btcli wallet new_coldkey --wallet.name validator
btcli wallet new_hotkey --wallet.name validator --wallet.hotkey hk1
btcli subnet register --netuid 490 --subtensor.network test --wallet.name validator --wallet.hotkey hk1

NPA_WALLET=validator NPA_HOTKEY=hk1 npa-validator
```

If your hotkey matches `OWNER_HOTKEY`, the validator can additionally bootstrap
the first round and freeze rounds when their evaluation start time arrives.

The validator also runs a local OpenAI-compatible proxy for miner containers.
Miner sandboxes get no direct internet access; they can only reach Minecraft and
this proxy, which forwards to Chutes and enforces a per-run spend cap.

## Consensus mechanism

Each round:

1. Miners upload one `tar.gz` agent package before the round freezes.
2. At freeze, the backend publishes one frozen roster manifest with:
   - round id
   - freeze block hash / round seed
   - every admitted miner submission
3. All validators download the same roster and evaluate every miner with
   `mcbench.evaluate_multiple_agents(...)`.
4. Every validator uploads:
   - one `report.json` per miner
   - one `recording.mcpr` per miner
   - one raw scoreboard JSON for the round
5. After the scoreboard deadline, every validator downloads all scoreboards,
   applies stake-weighted averaging, picks the top miner, and sets a
   winner-take-all weight vector on chain.

## Config knobs

Set via environment variables.

| Var | Default | Meaning |
| --- | --- | --- |
| `NPA_NETWORK` | `test` | Bittensor network |
| `NPA_API_URL` | `https://api.neverplayalone.ai` | API base URL |
| `NPA_WALLET` | `default` | Wallet name |
| `NPA_HOTKEY` | `default` | Hotkey name |
| `NPA_MISSION_ID` | `resource_gathering` | mcbench mission id |
| `NPA_LOOP_POLL_SECONDS` | `12` | Validator loop poll cadence |
| `NPA_WORKSPACE_ROOT` | `/tmp/npa_validator` | Local validator round workspace |
| `NPA_MAX_PARALLEL_AGENTS` | `2` | Parallel mcbench agent slots |
| `NPA_FIRST_ROUND_START_AT` | unset | First round start time for owner bootstrap |
| `CHUTES_API_KEY` | unset | Upstream Chutes API key used by the validator proxy |
| `NPA_PROXY_ENABLED` | `1` | Enable validator-local Chutes proxy injection |
| `NPA_PROXY_PORT` | `18080` | Host port exposed to miner containers as the local proxy |
| `NPA_PROXY_ALLOWED_MODELS` | empty | Optional comma-separated Chutes model allowlist |
| `NPA_PROXY_DEFAULT_INPUT_PRICE_PER_1M_USD` | `0` | Fallback input token price used for spend control |
| `NPA_PROXY_DEFAULT_OUTPUT_PRICE_PER_1M_USD` | `0` | Fallback output token price used for spend control |
| `NPA_PROXY_MODEL_PRICES_JSON` | empty | Optional per-model pricing JSON |
| `NPA_PROXY_MAX_TOTAL_SPEND_USD` | `1.0` | Max total proxy spend per miner run |

Replace `NPA_OWNER_HOTKEY` before deploy.
