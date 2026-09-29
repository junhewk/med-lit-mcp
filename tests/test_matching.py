from __future__ import annotations

import unittest

from med_lit_mcp.matching import (
    AliasScanner,
    acronym_matches,
    candidates,
    contains_verbatim,
    find_acronym_definitions,
    name_key,
    similarity,
    split_acronym,
)


class MatchingTests(unittest.TestCase):
    def test_name_keys_fold_case_plurals_and_punctuation(self) -> None:
        cases = {
            "LLMs": "llm",
            "Large Language Models": "large language model",
            "large-language model": "large language model",
            "sepsis": "sepsis",
            "AIDS": "aids",
            "diagnosis": "diagnosis",
            "COVID-19": "covid 19",
            "Electronic Health Records": "electronic health record",
            "Studies": "study",
            "C++": "c++",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(name_key(value), expected)

    def test_acronyms(self) -> None:
        self.assertEqual(split_acronym("large language model (LLM)"), ("large language model", "LLM"))
        self.assertEqual(split_acronym("SDM (shared decision-making)"), ("shared decision-making", "SDM"))
        self.assertIsNone(split_acronym("patients (n = 45)"))
        self.assertIsNone(split_acronym("machine learning (ML) models (LLM)"))
        self.assertTrue(acronym_matches("EHRs", "electronic health records"))
        self.assertTrue(acronym_matches("RCT", "randomized controlled trial"))
        self.assertFalse(acronym_matches("WHO", "large language model"))
        text = "We used large language models (LLMs) in shared decision-making (SDM) during 2024 (n = 3)."
        self.assertEqual(
            find_acronym_definitions(text), {"llm": "large language models", "sdm": "shared decision-making"}
        )

    def test_similarity_flags_variants_without_merging(self) -> None:
        self.assertGreater(similarity("organisation", "organization")[0], 0.9)
        self.assertEqual(similarity("llm", "large language model"), (0.9, ["acronym of the other name"]))
        self.assertIn("one name contains the other (check specificity)", similarity("decision aid", "patient decision aid")[1])
        pool = [(1, "organization"), (2, "deep learning"), (3, "patient portal")]
        self.assertEqual([c[0] for c in candidates("organisation", pool)], [1])
        self.assertEqual(candidates("machine learning", pool), [])

    def test_verbatim_and_scanner(self) -> None:
        self.assertTrue(contains_verbatim("The  “shared decision-making” model", "shared decision–making"))
        self.assertFalse(contains_verbatim("abc", " "))
        scanner = AliasScanner([("large language model", 1), ("sdm", 2), ("a", 3)])
        self.assertEqual(scanner.scan("Large language models support SDM; SDM again."), {1: 1, 2: 2})
