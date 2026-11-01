# Nemotron 3 Nano: training-data provenance

An inventory of every dataset NVIDIA lists for `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` at revision
`bf77c317`, read on 2026-10-31. Machine-readable source:
`docs/nemotron-datasets.json`, written by `scripts/nemotron_inventory.py`. Sizes are the
bytes of the files in each Hugging Face repository at the revision read (mostly
compressed parquet or jsonl). Rows come from the Hugging Face dataset viewer; "n/a" means
the viewer has no count.

## How the list was built

- **The pinned model card** is the authority for what trained this model. Its front
  matter names 16 NVIDIA datasets, and its "Training, Testing, and Evaluation Datasets"
  section names 141 datasets in five tables: public, private third-party, private NVIDIA,
  crawled by NVIDIA, and NVIDIA synthetic.
- **The RL blend card** (`Nemotron-3-Nano-RL-Training-Blend`) lists its 7 component
  datasets.
- **NVIDIA's Hugging Face collections** (Post-Training, Pre-Training, v3 Pre-Training) were
  read too. They are still edited: the Post-Training collection now holds 61 datasets, most
  released in 2026 for later models. The card's own collection link (`nemotron-post-training-v3`)
  returns 404. A dataset that appears only in a collection and was created after the model's
  release (2025-12-15) is listed separately as not used for this model. That is inferred from
  the date; collections have no history.

Totals: 140 Hugging Face datasets probed, 122 named rows without a Hugging Face dataset.

Dates that bound the data, from the card: training data collected from 2013 to
2025-05-01; the GitHub crawl on 2025-04-29; Common Crawl snapshots up to CC-MAIN-2025-18;
some post-training sources collected 2025-10-02. Post-training cutoff 2025-11-28.

## Post-training: SFT and RL sets released with this model

All open (no gating), all fully downloadable.

| Dataset | Stage | Domain | Contents | License | Size | Rows | Created | Source |
|---|---|---|---|---|---|---|---|---|
| [nvidia/Nemotron-Competitive-Programming-v1](https://huggingface.co/datasets/nvidia/Nemotron-Competitive-Programming-v1) | SFT | code | Competitive programming reasoning, Python (2M+) and C++ (1M+), plus the InfiniByte subset | cc-by-4.0 | 189.8 GB | n/a | 2025-12-15 | card metadata |
| [nvidia/Nemotron-SWE-v1](https://huggingface.co/datasets/nvidia/Nemotron-SWE-v1) | SFT | code | OpenHands agent trajectories on R2E-Gym software-engineering tasks (the card says 59k in its text and 51,029 in its table; the viewer counts 24,875 rows) | cc-by-4.0 | 11.1 GB | 24,875 | 2025-12-14 | **not in the card**; created 2025-12-14, in the post-training collection |
| [nvidia/Nemotron-Math-v2](https://huggingface.co/datasets/nvidia/Nemotron-Math-v2) | SFT | math | gpt-oss-120b solutions (with and without Python tool use) to AoPS and Math StackExchange problems | cc-by-4.0, cc-by-sa-4.0 | 204.6 GB | 7,085,839 | 2025-12-15 | card metadata |
| [nvidia/Nemotron-Math-Proofs-v1](https://huggingface.co/datasets/nvidia/Nemotron-Math-Proofs-v1) | SFT | math | Natural-language proof problems, Lean 4 formalizations and proofs | cc-by-sa-4.0 | 29.5 GB | 243,314 | 2025-12-14 | card metadata |
| [nvidia/Nemotron-Agentic-v1](https://huggingface.co/datasets/nvidia/Nemotron-Agentic-v1) | SFT | agentic | Synthetic multi-turn tool-use trajectories | cc-by-4.0 | 5.8 GB | n/a | 2025-12-14 | card metadata |
| [nvidia/Nemotron-Instruction-Following-Chat-v1](https://huggingface.co/datasets/nvidia/Nemotron-Instruction-Following-Chat-v1) | SFT | chat | Chat, instruction following, JSON structured output | cc-by-4.0 | 7.1 GB | 320,426 | 2025-12-14 | card metadata |
| [nvidia/Nemotron-Science-v1](https://huggingface.co/datasets/nvidia/Nemotron-Science-v1) | SFT | science | Science MCQA and reasoning traces (GPQA-style) | cc-by-4.0 | 2.6 GB | 226,334 | 2025-12-14 | card metadata |
| [nvidia/Nemotron-3-Nano-RL-Training-Blend](https://huggingface.co/datasets/nvidia/Nemotron-3-Nano-RL-Training-Blend) | RL | mixed | The RL blend used for this model, with mixing ratios; the components follow | odc-by | 6.9 GB | n/a | 2025-12-13 | card metadata |

SFT total: 450.5 GB. Code and math SFT: 435.0 GB.

RL blend components (from the blend card):

| Dataset | License | Size | Rows | Created | Access |
|---|---|---|---|---|---|
| [BytedTsinghua-SIA/DAPO-Math-17k](https://huggingface.co/datasets/BytedTsinghua-SIA/DAPO-Math-17k) | apache-2.0 | 299 MB | 1,791,700 | 2025-03-17 | open |
| [Skywork/Skywork-OR1-RL-Data](https://huggingface.co/datasets/Skywork/Skywork-OR1-RL-Data) | not stated | 823 MB | 119,112 | 2025-04-12 | open |
| [nvidia/Nemotron-RL-agent-workplace_assistant](https://huggingface.co/datasets/nvidia/Nemotron-RL-agent-workplace_assistant) | cc-by-4.0 | 30 MB | 1,800 | 2025-11-14 | open |
| [nvidia/Nemotron-RL-coding-competitive_coding](https://huggingface.co/datasets/nvidia/Nemotron-RL-coding-competitive_coding) | cc-by-sa-4.0 | 15.7 GB | 16,083 | 2025-12-12 | open |
| [nvidia/Nemotron-RL-instruction_following](https://huggingface.co/datasets/nvidia/Nemotron-RL-instruction_following) | odc-by | 78 MB | n/a | 2025-11-14 | open |
| [nvidia/Nemotron-RL-instruction_following-structured_outputs](https://huggingface.co/datasets/nvidia/Nemotron-RL-instruction_following-structured_outputs) | cc-by-4.0 | 96 MB | 9,949 | 2025-11-13 | open |
| [nvidia/Nemotron-RL-knowledge-mcqa](https://huggingface.co/datasets/nvidia/Nemotron-RL-knowledge-mcqa) | cc-by-4.0 | 757 MB | 685,573 | 2025-11-14 | open |

The RL environments themselves are released as NeMo Gym (GitHub), not as datasets.

## Pretraining: NVIDIA sets named in the card

| Dataset | Contents | License | Size | Created | Access with the current token |
|---|---|---|---|---|---|
| [nvidia/Nemotron-Pretraining-Code-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Code-v1) | GitHub code as **metadata only** (24.3 GB, pointers to files); synthetic code in full (221.9 GB) | other | 246.2 GB | 2025-08-14 | gated (manual), **not granted** |
| [nvidia/Nemotron-Pretraining-Code-v2](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Code-v2) | **Metadata only** for 377M more GitHub files (20.9 GB); synthetic code in full (886.3 GB) | other | 907.1 GB | 2025-11-25 | gated (manual), **not granted** |
| [nvidia/Nemotron-CC-Code-v1](https://huggingface.co/datasets/nvidia/Nemotron-CC-Code-v1) | 427.9B tokens of code pages from Common Crawl, text in full | cc-by-4.0, cc-by-sa-4.0, gfdl, other | 563.2 GB | 2025-12-06 | gated (manual), **not granted** |
| [nvidia/Nemotron-Pretraining-SFT-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-SFT-v1) | SFT-style pretraining data: math 160.8 GB, general 95.5 GB, code 59.3 GB | other | 315.6 GB | 2025-08-14 | gated (manual), **not granted** |
| [nvidia/Nemotron-CC-v2](https://huggingface.co/datasets/nvidia/Nemotron-CC-v2) | English Common Crawl, organic and synthetic rephrasings | other | 10,333.0 GB | 2025-08-14 | gated (manual), **not granted** |
| [nvidia/Nemotron-CC-v2.1](https://huggingface.co/datasets/nvidia/Nemotron-CC-v2.1) | Common Crawl update for Nemotron 3 (2.5T tokens) | other | 4,590.7 GB | 2025-12-12 | gated (manual), **not granted** |
| [nvidia/Nemotron-CC-Math-v1](https://huggingface.co/datasets/nvidia/Nemotron-CC-Math-v1) | Math pages from Common Crawl (133B tokens) | other | 259.9 GB | 2025-08-14 | gated (auto), **not granted** |
| [nvidia/Nemotron-Pretraining-Specialized-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Specialized-v1) | Synthetic STEM, scientific coding, wiki rewrites | cc-by-sa-4.0, cc-by-4.0, gfdl, other | 350.9 GB | 2025-12-14 | open |
| [nvidia/Nemotron-Pretraining-Dataset-sample](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Dataset-sample) | Small ungated sample of the pretraining and SFT corpora | other | 47 MB | 2025-08-14 | open |

**The GitHub code is not downloadable from these repositories.** The card's 747.4B-token
GitHub crawl, plus the roughly 340B tokens added in Code-v2, is released only as metadata
describing each file. The file contents have to be fetched from the original source. The
synthetic code and the Common Crawl code pages are released in full.

Gated sets need an access request on Hugging Face under NVIDIA's Data Access Agreement
for Model Training ("manual" means NVIDIA approves each request). Accepting it is the
operator's step.

## Other NVIDIA datasets cited in the card

Earlier releases cited as sources or seeds in the card's tables.

| Dataset | Cited in | License | Size | Rows | Created | Access |
|---|---|---|---|---|---|---|
| [nvidia/Aegis-AI-Content-Safety-Dataset-2.0](https://huggingface.co/datasets/nvidia/Aegis-AI-Content-Safety-Dataset-2.0) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 28 MB | 33,416 | 2025-01-10 | open |
| [nvidia/HelpSteer2](https://huggingface.co/datasets/nvidia/HelpSteer2) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 41 MB | 21,362 | 2024-06-02 | open |
| [nvidia/HelpSteer3](https://huggingface.co/datasets/nvidia/HelpSteer3) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | cc-by-4.0 | 304 MB | 132,937 | 2025-03-13 | open |
| [nvidia/Nemotron-MIND](https://huggingface.co/datasets/nvidia/Nemotron-MIND) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 263.7 GB | 2,992,528 | 2025-04-18 | open |
| [nvidia/Nemotron-Personas-USA](https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 2.7 GB | 1,000,000 | 2025-06-09 | open |
| [nvidia/Nemotron-PrismMath](https://huggingface.co/datasets/nvidia/Nemotron-PrismMath) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 7.2 GB | 1,002,595 | 2025-05-12 | open |
| [nvidia/OpenCodeReasoning](https://huggingface.co/datasets/nvidia/OpenCodeReasoning) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 9.7 GB | 337,766 | 2025-04-01 | open |
| [nvidia/OpenCodeReasoning-2](https://huggingface.co/datasets/nvidia/OpenCodeReasoning-2) | datasets (public) | cc-by-4.0 | 49.4 GB | 220,000 | 2025-05-03 | open |
| [nvidia/OpenMathReasoning](https://huggingface.co/datasets/nvidia/OpenMathReasoning) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 49.5 GB | 5,678,317 | 2025-04-22 | open |

## Third-party Hugging Face datasets cited in the card

The card does not say which training stage each one went into.

| Dataset | Cited in | License | Size | Rows | Access |
|---|---|---|---|---|---|
| [AI-MO/NuminaMath-CoT](https://huggingface.co/datasets/AI-MO/NuminaMath-CoT) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | apache-2.0 | 1.2 GB | n/a | open |
| [BAAI/TACO](https://huggingface.co/datasets/BAAI/TACO) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 7.2 GB | n/a | open |
| [EleutherAI/arithmetic](https://huggingface.co/datasets/EleutherAI/arithmetic) | datasets (public) | not stated | 1 MB | 20,000 | open |
| [EricLu/SCP-116K](https://huggingface.co/datasets/EricLu/SCP-116K) | NVIDIA-Sourced Synthetic Datasets | cc-by-nc-sa-4.0 | 5.5 GB | 259,092 | open |
| [EssentialAI/essential-web-v1.0](https://huggingface.co/datasets/EssentialAI/essential-web-v1.0) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | odc-by | 26,171.0 GB | n/a | open |
| [GAIR/LIMO](https://huggingface.co/datasets/GAIR/LIMO) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 16 MB | 817 | open |
| [GPUMODE/KernelBook](https://huggingface.co/datasets/GPUMODE/KernelBook) | NVIDIA-Sourced Synthetic Datasets | other | 504 MB | 18,162 | open |
| [HuggingFaceFW/finepdfs](https://huggingface.co/datasets/HuggingFaceFW/finepdfs) | datasets (public) | odc-by | 5,375.6 GB | 476,178,356 | open |
| [HuggingFaceFW/fineweb-2](https://huggingface.co/datasets/HuggingFaceFW/fineweb-2) | datasets (public) | odc-by | 20,150.1 GB | 4,484,929,995 | open |
| [HuggingFaceTB/finemath](https://huggingface.co/datasets/HuggingFaceTB/finemath) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | odc-by | 149.4 GB | 48,283,984 | open |
| [II-Vietnam/IChO-IPhO-RL-v2-formated](https://huggingface.co/datasets/II-Vietnam/IChO-IPhO-RL-v2-formated) | NVIDIA-Sourced Synthetic Datasets | not stated | 2 MB | 1,262 | open |
| [JailbreakV-28K/JailBreakV-28k](https://huggingface.co/datasets/JailbreakV-28K/JailBreakV-28k) | NVIDIA-Sourced Synthetic Datasets | mit | 303 MB | 30,280 | open |
| [LLM360/MegaMath](https://huggingface.co/datasets/LLM360/MegaMath) | datasets (public) | odc-by | 1,118.2 GB | n/a | open |
| [Nexusflow/MultiverseMathHard](https://huggingface.co/datasets/Nexusflow/MultiverseMathHard) | datasets (public) | not stated | 0 MB | 100 | open |
| [OpenCoder-LLM/opc-sft-stage2](https://huggingface.co/datasets/OpenCoder-LLM/opc-sft-stage2) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | mit | 537 MB | 436,347 | open |
| [R2E-Gym/R2E-Gym-Subset](https://huggingface.co/datasets/R2E-Gym/R2E-Gym-Subset) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 944 MB | 4,578 | open |
| [RyokoAI/ShareGPT52K](https://huggingface.co/datasets/RyokoAI/ShareGPT52K) | NVIDIA-Sourced Synthetic Datasets | cc0-1.0 | 2.9 GB | n/a | open |
| [SWE-Gym/SWE-Gym](https://huggingface.co/datasets/SWE-Gym/SWE-Gym) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | mit | 44 MB | 2,438 | open |
| [SWE-bench/SWE-smith](https://huggingface.co/datasets/SWE-bench/SWE-smith) | NVIDIA-Sourced Synthetic Datasets | mit | 278 MB | 59,136 | open |
| [Salesforce/xlam-function-calling-60k](https://huggingface.co/datasets/Salesforce/xlam-function-calling-60k) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | cc-by-4.0 | 98 MB | 60,000 | gated (auto), granted |
| [SynthLabsAI/Big-Math-RL-Verified](https://huggingface.co/datasets/SynthLabsAI/Big-Math-RL-Verified) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | apache-2.0 | 33 MB | n/a | gated (auto), **not granted** |
| [TigerResearch/tigerbot-kaggle-leetcodesolutions-en-2k](https://huggingface.co/datasets/TigerResearch/tigerbot-kaggle-leetcodesolutions-en-2k) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | apache-2.0 | 5 MB | 2,048 | open |
| [Vikhrmodels/physics_big](https://huggingface.co/datasets/Vikhrmodels/physics_big) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 535 MB | 2,332 | open |
| [allenai/WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | odc-by | 3.4 GB | 837,989 | open |
| [allenai/c4](https://huggingface.co/datasets/allenai/c4) | NVIDIA-Sourced Synthetic Datasets | odc-by | 13,804.4 GB | 114,005,516 | open |
| [allenai/cosmos_qa](https://huggingface.co/datasets/allenai/cosmos_qa) | datasets (public) | cc-by-4.0 | 0 MB | n/a | open |
| [allenai/peS2o](https://huggingface.co/datasets/allenai/peS2o) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | odc-by | 307.7 GB | n/a | open |
| [bespokelabs/Bespoke-Stratos-17k](https://huggingface.co/datasets/bespokelabs/Bespoke-Stratos-17k) | datasets (public) | apache-2.0 | 126 MB | 16,710 | open |
| [cais/mmlu](https://huggingface.co/datasets/cais/mmlu) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | mit | 270 MB | 231,400 | open |
| [deepmind/aqua_rat](https://huggingface.co/datasets/deepmind/aqua_rat) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 52 MB | 195,950 | open |
| [demelin/moral_stories](https://huggingface.co/datasets/demelin/moral_stories) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | mit | 259 MB | 720,000 | open |
| [garrethlee/simple-arithmetic-problems](https://huggingface.co/datasets/garrethlee/simple-arithmetic-problems) | datasets (public) | not stated | 7 MB | 400,000 | open |
| [glaiveai/glaive-function-calling-v2](https://huggingface.co/datasets/glaiveai/glaive-function-calling-v2) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | apache-2.0 | 271 MB | 112,960 | open |
| [gretelai/gretel-safety-alignment-en-v1](https://huggingface.co/datasets/gretelai/gretel-safety-alignment-en-v1) | NVIDIA-Sourced Synthetic Datasets | not stated | 17 MB | 16,722 | open |
| [ise-uiuc/Magicoder-Evol-Instruct-110K](https://huggingface.co/datasets/ise-uiuc/Magicoder-Evol-Instruct-110K) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 255 MB | 111,183 | open |
| [kensho/DocFinQA](https://huggingface.co/datasets/kensho/DocFinQA) | NVIDIA-Sourced Synthetic Datasets | mit | 4.7 GB | 7,437 | open |
| [lmarena-ai/arena-human-preference-140k](https://huggingface.co/datasets/lmarena-ai/arena-human-preference-140k) | NVIDIA-Sourced Synthetic Datasets | cc-by-4.0 | 1.6 GB | 135,634 | open |
| [lmsys/lmsys-chat-1m](https://huggingface.co/datasets/lmsys/lmsys-chat-1m) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | not stated | 1.5 GB | n/a | gated (auto), **not granted** |
| [lucasmccabe/logiqa](https://huggingface.co/datasets/lucasmccabe/logiqa) | NVIDIA-Sourced Synthetic Datasets | not stated | 0 MB | 8,678 | open |
| [meta-math/MetaMathQA](https://huggingface.co/datasets/meta-math/MetaMathQA) | datasets (public) | mit | 396 MB | 395,000 | open |
| [open-r1/OpenR1-Math-220k](https://huggingface.co/datasets/open-r1/OpenR1-Math-220k) | NVIDIA-Sourced Synthetic Datasets | apache-2.0 | 12.6 GB | 450,258 | open |
| [open-web-math/open-web-math](https://huggingface.co/datasets/open-web-math/open-web-math) | datasets (public) | not stated | 27.4 GB | 6,315,233 | open |
| [openlifescienceai/medmcqa](https://huggingface.co/datasets/openlifescienceai/medmcqa) | datasets (public) | apache-2.0 | 88 MB | 193,155 | open |
| [sagnikrayc/mctest](https://huggingface.co/datasets/sagnikrayc/mctest) | datasets (public) | other | 0 MB | 2,640 | open |
| [tasksource/social-chemestry-101](https://huggingface.co/datasets/tasksource/social-chemestry-101) | NVIDIA-Sourced Synthetic Datasets; datasets (public) | not stated | 147 MB | 355,922 | open |
| [xw27/scibench](https://huggingface.co/datasets/xw27/scibench) | NVIDIA-Sourced Synthetic Datasets | not stated | 0 MB | 692 | open |

## Named in the card, not on Hugging Face

### Public sources hosted elsewhere (35)

Downloadable from the source, not packaged by NVIDIA.

- [GSM8K](https://github.com/openai/grade-school-math) (collected: 4/23/2025)
- [CC-NEWS](https://commoncrawl.org/blog/news-dataset-available) (collected: 4/23/2025)
- [Common Crawl](https://commoncrawl.org/) (collected: 4/23/2025)
- [Wikimedia](https://dumps.wikimedia.org/) (collected: 4/23/2025)
- [Open Textbook Library - CC BY-SA & GNU subset and OpenStax - CC BY-SA subset](https://open.umn.edu/opentextbooks/textbooks/) (collected: 4/23/2025)
- [Court Listener](https://www.courtlistener.com/help/api/bulk-data/) (collected: Legacy Download)
- [BioRxiv](https://www.biorxiv.org/tdm) (collected: Legacy Download)
- [PMC Open Access Subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/) (collected: Legacy Download)
- [OpenWebText2](https://openwebtext2.readthedocs.io/en/latest/) (collected: Legacy Download)
- [Stack Exchange Data Dump](https://archive.org/details/stackexchange) (collected: Legacy Download)
- [PubMed Abstracts](https://github.com/thoppe/The-Pile-PubMed) (collected: Legacy Download)
- [NIH ExPorter](https://exporter.nih.gov/ExPORTER_Catalog.aspx) (collected: Legacy Download)
- [arXiv](https://info.arxiv.org/help/bulk_data/index.html) (collected: Legacy Download)
- [BigScience Workshop Datasets](https://github.com/bigscience-workshop/bigscience/tree/master/train/tr11-176B-ml#datasets) (collected: Legacy Download)
- [Reddit Dataset](https://files.pushshift.io/reddit/) (collected: Legacy Download)
- [SEC's Electronic Data Gathering, Analysis, and Retrieval (EDGAR)](https://www.sec.gov/search-filings) (collected: Legacy Download)
- [Advanced Mathematical Problem Solving](https://github.com/hendrycks/math?tab=readme-ov-file) (collected: Legacy Download)
- [MathPile](https://github.com/GAIR-NLP/MathPile/) (collected: Legacy Download)
- [PMC Article](https://pmc.ncbi.nlm.nih.gov/tools/textmining/) (collected: Legacy Download)
- [FLAN](https://github.com/google-research/FLAN) (collected: Legacy Download)
- [Advanced Reasoning Benchmark](https://github.com/TheDuckAI/arb) (collected: Legacy Download)
- [SciBench](https://github.com/mandyyyyii/scibench) (collected: Legacy Download)
- [WikiTableQuestions](https://huggingface.co/datasets/wikitablequestions) (collected: Legacy Download)
- [FinQA](https://finqasite.github.io/) (collected: Legacy Download)
- [Riddles](https://github.com/crawsome/riddles) (collected: Legacy Download)
- [Problems in Elementary Mathematics for Home Study](https://archive.org/details/AntonovVygodskyNikitinSankinProblemsInElementaryMathematicsForHomeStudyMir1982) (collected: Legacy Download)
- [AI2's Reasoning Challenge](https://huggingface.co/datasets/ai2_arc) (collected: Legacy Download)
- [OpenBookQA](https://github.com/allenai/OpenBookQA) (collected: Legacy Download)
- [The Common Pile v0.1](https://huggingface.co/common-pile) (collected: Legacy Download)
- [WorkBench](https://github.com/olly-styles/WorkBench/tree/main/data/raw) (collected: 10/2/2025)
- [News Commentary](https://opus.nlpl.eu/News-Commentary.php) (collected: 10/2/2025)
- [FastChat](https://github.com/lm-sys/FastChat/blob/main/playground/data/dummy.json) (collected: 10/2/2025)
- [HotpotQA](https://huggingface.co/hotpot_qa/datasets) (collected: 10/2/2025)
- [SQuAD2.0](https://rajpurkar.github.io/SQuAD-explorer/) (collected: 10/2/2025)
- [NLTK Words Lists](https://www.nltk.org/nltk_data/) (collected: 10/2/2025)

### Crawled by NVIDIA (4)

Released in processed form where the sets above say so (Nemotron-CC, Code metadata); not otherwise.

- English Common Crawl: 3.36T tokens, collected 4/8/2025
- English Common Crawl 1.1: Not disclosed tokens, collected 10/2/2025
- Multilingual Common Crawl: 812.7B tokens, collected 5/1/2025
- GitHub Crawl: 747.4B tokens, collected 4/29/2025

### Private, third party (4)

Named only.

- Global Regulation
- TAUS Translation Memory
- Scale HLE
- HackerRank Coding

### Private, NVIDIA (5)

Named only.

- Simple Minesweeper
- Simple Sudoku
- Multitool Typewriter Hard
- Machine Translation of News Commentary and TAUS Translation Memory
- Machine Translation of STEM data using Qwen2.5-14B-Instruct

### NVIDIA synthetic data (74)

Named only, unless the row links a dataset (those are in the tables above). Many are probably contained in the released Pretraining-SFT, Specialized or post-training sets, but the card does not map them.

- Synthetic Art of Problem Solving from DeepSeek-R1: 40B tokens
- Synthetic Moral Stories and Social Chemistry from Mixtral-8x22B-v0.1: 327M tokens
- Synthetic Social Sciences seeded with OpenStax from DeepSeek-V3, Mixtral-8x22B-v0.1, and Qwen2.5-72B: 83.6M tokens
- Synthetic Health Sciences seeded with OpenStax from DeepSeek-V3, Mixtral-8x22B-v0.1, and Qwen2.5-72B: 9.7M tokens
- Synthetic STEM seeded with OpenStax, Open Textbook Library, and GSM8K from DeepSeek-R1, DeepSeek-V3, DeepSeek-V3-0324, and Qwen2.5-72B: 175M tokens
- Synthetic Question Answering Data from Papers and Permissible Books from Qwen2.5-72B-Instruct: 350M tokens
- Nemotron-CC-Math-4plus: 52.3B tokens
- Nemotron-CC-Math-3: 80.9B tokens
- Synthetic AGIEval seeded with AQUA-RAT, LogiQA, and AR-LSAT from DeepSeek-V3 and DeepSeek-V3-0324: 4.0B tokens
- Synthetic AGIEval seeded with AQUA-RAT, LogiQA, and AR-LSAT from Qwen3-30B-A3B: 4.2B tokens
- Synthetic Art of Problem Solving from Qwen2.5-32B-Instruct, Qwen2.5-Math-72B, Qwen2.5-Math-7B, and Qwen2.5-72B-Instruct
- Synthetic MMLU Auxiliary Train from DeepSeek-R1: 0.5B tokens
- Synthetic Long Context Continued Post-Training Data from Papers and Permissible Books from Qwen2.5-72B-Instruct
- Synthetic Common Crawl from Qwen3-30B-A3B and Mistral-Nemo-12B-Instruct: 415.8B tokens
- Synthetic Multilingual Data from Common Crawl from Qwen3-30B-A3B
- Synthetic Multilingual Data from Wikimedia from Qwen3-30B-A3B
- Synthetic Math Data from Wikimedia from Nemotron-4-340B-Instruct
- Synthetic Common Crawl Code from phi-4: 427.9B tokens
- Synthetic Scientific Coding from Qwen3-235B-A22B: 1.2B tokens
- Tool Calling Data: 26.2B tokens
- Synthetic Essential-Web from QwQ-32B: 28.1B tokens
- Translated Synthetic Crawl: 389.9B tokens
- Translated Synthetic Wikipedia: 7.9B tokens
- Synthetic Art of Problem Solving from gpt-oss-120b and Qwen2.5-32B-Instruct: Undisclosed tokens
- Synthetic Stack Exchange from gpt-oss-120b and Qwen2.5-32B-Instruct: Undisclosed tokens
- Synthetic OpenCodeReasoning from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic HackerRank Coding from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic SWE-Gym from Qwen3-Coder-480B-A35B-Instruct: Undisclosed tokens
- Synthetic Art of Problem Solving and Stack Exchange from gpt-oss-120b, Qwen2.5-32B-Instruct, and Goedel-Prover-V2-32B: Undisclosed tokens
- Synthetic Multilingual Science and Code data from DeepSeek-R1, DeepSeek-R1-0528, Qwen2.5-32B-Instruct, and Qwen3-235B-A22B, translated with Qwen2.5-32B-Instruct and Qwen2.5-14B-Instruct: Undisclosed tokens
- Synthetic Safety from DeepSeek-R1-0528, gpt-oss-120b and Mixtral-8x7B-v0.1: Undisclosed tokens
- Synthetic STEM from Qwen3-235B-A22B-Instruct-2507 and gpt-oss-120b: Undisclosed tokens
- Synthetic KernelBook from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Tool Calling from Qwen3-235B-A22B-Thinking-2507 and Qwen3-Next-80B-A3B-Thinking: Undisclosed tokens
- Synthetic Chat from gpt-oss-120b, Mixtral-8x22B-Instruct-v0.1, Qwen3-235B-A22B-Instruct-2507 , and Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic Long Context from Qwen3-235B-A22B-Instruct-2507: Undisclosed tokens
- Synthetic Tool Use Interactive Agent from gpt-oss-120b, DeepSeek-R1-0528, Qwen3-32B, and Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic STEM from Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic DocFinQA and SWE-smith from Qwen3-Coder-480B-A35B-Instruct and Kimi-K2-Thinking: Undisclosed tokens
- Synthetic Math from gpt-oss-120b and Qwen2.5-32B-Instruct: Undisclosed tokens
- Synthetic Essential-Web from gpt-oss-120b: Undisclosed tokens
- Synthetic Scale HLE from gpt-oss-120b: Undisclosed tokens
- Synthetic CDQuestions from gpt-oss-120b: Undisclosed tokens
- Synthetic Stack Exchange from gpt-oss-120b: Undisclosed tokens
- Synthetic GPQA from gpt-oss-120b and Qwen2.5-32B-Instruct: Undisclosed tokens
- Synthetic Vedantu from gpt-oss-120b: Undisclosed tokens
- Synthetic SWE-Gym and R2E-Gym-Subset from Qwen3-Coder-480B-A35B-Instruct: Undisclosed tokens
- Synthetic SWE-Gym from Qwen3-Coder-480B-A35B-Instruct: Undisclosed tokens
- Synthetic SWE-Gym and R2E-Gym-Subset from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic HelpSteer, LMSYS-Chat-1M, and Nemotron-Personas-USA from gpt-oss-120b, Qwen3-235B-A22B-Instruct-2507, and Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic Structured Outputs from Qwen3-30B-A3B-Instruct-2507, Qwen3-30B-A3B-Thinking-2507, Qwen3-235B-A22B-Instruct-2507, and Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic Search STEM MCQ from Qwen3-235B-A22B and DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Search STEM OPENQ from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic OpenSTEM from Qwen2.5-32B-Instruct and DeepSeek-R1-0528: Undisclosed tokens
- Synthetic MCQ from Qwen2.5-32B-Instruct and DeepSeek-R1-0528: Undisclosed tokens
- Synthetic MCQ10 from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic MCQ4 from Qwen3-235B-A22B, DeepSeek-R1-0528, and Qwen3-235B-A22B-Instruct-2507: Undisclosed tokens
- Synthetic OpenMathReasoning from gpt-oss-120b and Qwen2.5-32B-Instruct: Undisclosed tokens
- Synthetic Offline Search MCQA HLE from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Offline Search MCQA GPQA from Qwen3-235B-A22B and DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Human Preference from QwQ-32B, Qwen3-30B-A3B, Qwen3-235B-A22B, Qwen3-235B-A22B-Instruct-2507, Mistral-Small-3.1-24B-Instruct-2503, Mistral-Small-3.2-24B-Instruct-2506, MiniMax-M1-80k, MiniMax-M1-40k, Kimi-K2-Instruct, DeepSeek-V3-0324, DeepSeek-R1-0528: Undisclosed tokens
- Synthetic WildChat-1M and arena-human-preference-140k from DeepSeek-R1, gemma-2-2b-it, gemma-3-27b-it, gpt-oss-20b, gpt-oss-120b, Mistral-7B-Instruct-v0.3, Mixtral-8x22B-Instruct-v0.1, Nemotron-4-340B-Instruct, NVIDIA-Nemotron-Nano-9B-v2, Phi-4-mini-instruct, Phi-3-small-8k-instruct, Phi-3-medium-4k-instruct, Qwen3-235B-A22B, QwQ-32B: Undisclosed tokens
- Synthetic Safety from DeepSeek-R1-0528, gpt-oss-120b, DeepSeek-R1-Distill-Qwen-7B, and Mixtral-8x7B-v0.1: Undisclosed tokens
- Synthetic Code from Qwen3-32B: Undisclosed tokens
- Synthetic OpenCodeReasoning from DeepSeek-R1: Undisclosed tokens
- Synthetic LIMO from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic SCP from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Stack Exchange from DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Common Crawl from Qwen3-30B-A3B: Undisclosed tokens
- Synthetic Wikipedia from Qwen3-30B-A3B: Undisclosed tokens
- Synthetic Essential-Web from Qwen3-30B-A3B and Qwen3-235B-A22B-Thinking-2507: Undisclosed tokens
- Synthetic Textbook Math from Qwen3-30B-A3B, Qwen3-235B-A22B, phi-4: Undisclosed tokens
- Synthetic Math and Code from DeepSeek-R1 and DeepSeek-R1-0528: Undisclosed tokens
- Synthetic Nemotron-Personas-USA from gpt-oss-120b and Qwen3-8B: Undisclosed tokens

## In NVIDIA's collections, released after this model (61)

Created after 2025-12-15 and not named in the card or the RL blend, so not used for this model
(inferred from the date). Listed for completeness; not candidates for this project.

| Dataset | Collection | Created | Size |
|---|---|---|---|
| [nvidia/Nemotron-Pretraining-Specialized-v1.1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Specialized-v1.1) | pre-training | 2026-01-16 | 34.9 GB |
| [nvidia/Nemotron-RL-Agentic-Conversational-Tool-Use-Pivot-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-Conversational-Tool-Use-Pivot-v1) | post-training | 2026-03-06 | 1.8 GB |
| [nvidia/Nemotron-RL-Agentic-Function-Calling-Pivot-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-Function-Calling-Pivot-v1) | post-training | 2026-03-06 | 285 MB |
| [nvidia/Nemotron-RL-Agentic-SWE-Pivot-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-SWE-Pivot-v1) | post-training | 2026-03-06 | 5.1 GB |
| [nvidia/Nemotron-RL-Identity-Following-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Identity-Following-v1) | post-training | 2026-03-06 | 16 MB |
| [nvidia/Nemotron-RL-Instruction-Following-Adversarial-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-Adversarial-v1) | post-training | 2026-03-06 | 8 MB |
| [nvidia/Nemotron-RL-Instruction-Following-Calendar-v2](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-Calendar-v2) | post-training | 2026-03-06 | 140 MB |
| [nvidia/Nemotron-RL-Instruction-Following-MultiTurnChat-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-MultiTurnChat-v1) | post-training | 2026-03-06 | 187 MB |
| [nvidia/Nemotron-RLHF-GenRM-v1](https://huggingface.co/datasets/nvidia/Nemotron-RLHF-GenRM-v1) | post-training | 2026-03-08 | 5.4 GB |
| [nvidia/Nemotron-SFT-Agentic-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Agentic-v2) | post-training | 2026-03-08 | 21.9 GB |
| [nvidia/Nemotron-SFT-Competitive-Programming-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Competitive-Programming-v2) | post-training | 2026-03-08 | 97.3 GB |
| [nvidia/Nemotron-SFT-Instruction-Following-Chat-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Instruction-Following-Chat-v2) | post-training | 2026-03-08 | 16.0 GB |
| [nvidia/Nemotron-SFT-Math-v3](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Math-v3) | post-training | 2026-03-08 | 154.1 GB |
| [nvidia/Nemotron-SFT-SWE-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-SWE-v2) | post-training | 2026-03-08 | 18.1 GB |
| [nvidia/Nemotron-SFT-Safety-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Safety-v1) | post-training | 2026-03-08 | 155 MB |
| [nvidia/Nemotron-SpecializedDomains-Finance-v1](https://huggingface.co/datasets/nvidia/Nemotron-SpecializedDomains-Finance-v1) | post-training | 2026-03-08 | 31.9 GB |
| [nvidia/Nemotron-RL-ReasoningGym-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-ReasoningGym-v1) | post-training | 2026-03-10 | 31 MB |
| [nvidia/Nemotron-RL-Safety-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Safety-v1) | post-training | 2026-03-10 | 295 MB |
| [nvidia/Nemotron-SFT-Multilingual-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Multilingual-v1) | post-training | 2026-03-10 | 94.6 GB |
| [nvidia/Nemotron-RL-Super-Training-Blends](https://huggingface.co/datasets/nvidia/Nemotron-RL-Super-Training-Blends) | post-training | 2026-03-11 | 26.8 GB |
| [nvidia/Nemotron-SFT-OpenCode-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-OpenCode-v1) | post-training | 2026-03-13 | 32.7 GB |
| [nvidia/Nemotron-RL-ARC-AGI-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-ARC-AGI-v1) | post-training | 2026-05-26 | 443 MB |
| [nvidia/Nemotron-RL-CFBench-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-CFBench-v1) | post-training | 2026-05-26 | 50 MB |
| [nvidia/Nemotron-RL-Instruction-Following-Citation-Formatting-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-Citation-Formatting-v1) | post-training | 2026-05-26 | 58 MB |
| [nvidia/Nemotron-RL-Instruction-Following-Free-Form-Formatting-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-Free-Form-Formatting-v1) | post-training | 2026-05-26 | 43 MB |
| [nvidia/Nemotron-RL-InverseIFEval-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-InverseIFEval-v1) | post-training | 2026-05-26 | 13 MB |
| [nvidia/Nemotron-RL-Multichallenge-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Multichallenge-v1) | post-training | 2026-05-26 | 282 MB |
| [nvidia/Nemotron-RL-QA-Abstention-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-QA-Abstention-v1) | post-training | 2026-05-26 | 5 MB |
| [nvidia/Nemotron-RL-SysBench-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-SysBench-v1) | post-training | 2026-05-26 | 49 MB |
| [nvidia/Nemotron-SFT-ARC-AGI-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-ARC-AGI-v1) | post-training | 2026-05-26 | 18.4 GB |
| [nvidia/Nemotron-SFT-CUDA-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-CUDA-v1) | post-training | 2026-05-26 | 83 MB |
| [nvidia/Nemotron-SFT-Instruction-Following-Chat-v3](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Instruction-Following-Chat-v3) | post-training | 2026-05-26 | 20.3 GB |
| [nvidia/Nemotron-SFT-Math-v4](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Math-v4) | post-training | 2026-05-26 | 5.5 GB |
| [nvidia/Nemotron-SFT-Multilingual-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Multilingual-v2) | post-training | 2026-05-26 | 12.5 GB |
| [nvidia/Nemotron-SFT-SWE-v3](https://huggingface.co/datasets/nvidia/Nemotron-SFT-SWE-v3) | post-training | 2026-05-26 | 11.7 GB |
| [nvidia/Nemotron-SFT-Safety-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Safety-v2) | post-training | 2026-05-26 | 576 MB |
| [nvidia/Nemotron-SFT-Science-v2](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Science-v2) | post-training | 2026-05-26 | 52.7 GB |
| [nvidia/Nemotron-Pretraining-Code-v3](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Code-v3) | pre-training | 2026-05-28 | 8.2 GB |
| [nvidia/Nemotron-Math-Proofs-v2](https://huggingface.co/datasets/nvidia/Nemotron-Math-Proofs-v2) | post-training | 2026-06-01 | 17.1 GB |
| [nvidia/Nemotron-RL-Math-v2](https://huggingface.co/datasets/nvidia/Nemotron-RL-Math-v2) | post-training | 2026-06-01 | 7 MB |
| [nvidia/Nemotron-RL-litmus-bench-v0.1](https://huggingface.co/datasets/nvidia/Nemotron-RL-litmus-bench-v0.1) | post-training | 2026-06-01 | 6 MB |
| [nvidia/Nemotron-RL-Agentic-Indirect-Prompt-Injection-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-Indirect-Prompt-Injection-v1) | post-training | 2026-06-02 | 12 MB |
| [nvidia/Nemotron-RL-Instruction-Following-Structured-Outputs-v2](https://huggingface.co/datasets/nvidia/Nemotron-RL-Instruction-Following-Structured-Outputs-v2) | post-training | 2026-06-02 | 763 MB |
| [nvidia/Nemotron-RL-Ultra-Training-Blends](https://huggingface.co/datasets/nvidia/Nemotron-RL-Ultra-Training-Blends) | post-training | 2026-06-02 | 17.1 GB |
| [nvidia/Nemotron-Pretraining-Legal-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Legal-v1) | pre-training | 2026-06-03 | 7.0 GB |
| [nvidia/Nemotron-Pretraining-Specialized-v1.2](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Specialized-v1.2) | pre-training | 2026-06-03 | 53.6 GB |
| [nvidia/Nemotron-RL-Science-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Science-v1) | post-training | 2026-06-03 | 266 MB |
| [nvidia/Nemotron-RL-Agentic-Terminal-Pivot-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-Terminal-Pivot-v1) | post-training | 2026-08-06 | 1.4 GB |
| [nvidia/Nemotron-RL-Lightning-Training-Blend](https://huggingface.co/datasets/nvidia/Nemotron-RL-Lightning-Training-Blend) | post-training | 2026-08-07 | 3.9 GB |
| [nvidia/Nemotron-SFT-SWE-v3.5](https://huggingface.co/datasets/nvidia/Nemotron-SFT-SWE-v3.5) | post-training | 2026-08-11 | 269 MB |
| [nvidia/Nemotron-Pretraining-Indic-CC-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Indic-CC-v1) | pre-training | 2026-09-04 | 9,508.2 GB |
| [nvidia/Nemotron-Image-Training-v3.5](https://huggingface.co/datasets/nvidia/Nemotron-Image-Training-v3.5) | post-training | 2026-10-07 | 28.1 GB |
| [nvidia/Nemotron-RL-Agentic-Finance-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Agentic-Finance-v1) | post-training | 2026-10-14 | 166 MB |
| [nvidia/Nemotron-RL-Finance-Tool-Calling-v1](https://huggingface.co/datasets/nvidia/Nemotron-RL-Finance-Tool-Calling-v1) | post-training | 2026-10-14 | 1.2 GB |
| [nvidia/Nemotron-SFT-Agentic-Finance-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Agentic-Finance-v1) | post-training | 2026-10-14 | 28 MB |
| [nvidia/Nemotron-SFT-Agentic-v4](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Agentic-v4) | post-training | 2026-10-14 | 18.9 GB |
| [nvidia/Nemotron-SFT-Finance-Document-Grounded-Reasoning-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Finance-Document-Grounded-Reasoning-v1) | post-training | 2026-10-14 | 1.0 GB |
| [nvidia/Nemotron-SFT-Long-Context-Transduction-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Long-Context-Transduction-v1) | post-training | 2026-10-14 | 1.2 GB |
| [nvidia/Nemotron-SFT-Long-Context-Translation-PG19-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Long-Context-Translation-PG19-v1) | post-training | 2026-10-14 | 2.2 GB |
| [nvidia/Nemotron-SFT-BBQ-v1](https://huggingface.co/datasets/nvidia/Nemotron-SFT-BBQ-v1) | post-training | 2026-10-15 | 8 MB |
| [nvidia/Nemotron-Pretraining-Indic-Specialized-v1](https://huggingface.co/datasets/nvidia/Nemotron-Pretraining-Indic-Specialized-v1) | pre-training | 2026-10-26 | 1,253.1 GB |
