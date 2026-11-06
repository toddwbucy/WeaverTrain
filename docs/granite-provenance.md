# Granite 4.2 30B source metadata

Read on 2026-11-06 before artifact setup under GRANITE-MODEL.

| Source | Revision | SHA256 |
| --- | --- | --- |
| [Model card][card] | `9e668ce1c538387ef24d3644e9b0606647762636` | `10d0e2b083c888814dc92a6308ca34d1c6fe01f344b54ff54905a8cbe5a80b06` |
| [IBM disclosure][disclosure] | `75355e6ef8c17d72a7b3736654ab525352f5e0b8` | `7cb6828b39230a8cc51c2cca0bd443f90fa6c0a239b9d486d2e939009b667801` |

The card identifies a dense Granite model with native Transformers support.
Its model license is Apache 2.0. The disclosure uses CDLA-Permissive-2.0.
The disclosure lists 93 dataset rows, including public, synthetic and nonpublic sources.
Webhose is one listed nonpublic source.
The disclosure is source metadata, not the complete training corpus.
It does not establish that the held-out sources were absent from earlier model training.
No listed source adds a dataset or target to this experiment.

The task uses the approved greedy decode rule even where the card suggests sampling.
Granite and Nemotron differ in model, earlier training, tokenizer, template and adapter size.
Report the two series separately. Do not attribute a difference to architecture alone.

The models stage checked 24 files against publisher digests at the pinned revision.
The manifest records 58,563,424,797 bytes and tree hash
`8b7aa9c7c6b38e45d27829b717edb5e69bded25a4ea1ca2a88d6a41afd1871b1`.
Artifact verification does not establish GPU fit or training support.

[card]: https://huggingface.co/ibm-granite/granite-4.2-30b/blob/9e668ce1c538387ef24d3644e9b0606647762636/README.md
[disclosure]: https://github.com/ibm-granite/granite-4.2-language-models/blob/75355e6ef8c17d72a7b3736654ab525352f5e0b8/disclosures/granite_4.2_30b_disclosure.json
