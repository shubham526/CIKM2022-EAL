import spacy
import re
from typing import List, Tuple, Any, Dict, Set
from spacy.tokens import Doc
import string
import gzip
from tqdm import tqdm
import joblib
import contextlib
import json
from pykson import Pykson, JsonObject, StringField, IntegerField, ListField, ObjectListField, ObjectField, Pykson, \
    BooleanField
from object_models import Location, Entity, AnnotatedText, AspectLinkExample, Aspect, Context
import torch


class TextProcessor:
    def __init__(self, model='en_core_web_sm'):
        try:
            self._model = spacy.load(model, disable=["ner", "parser"])
        except OSError:
            print(f"Downloading spaCy model {model}")
            spacy.cli.download(model)
            print(f"Finished downloading model")
            self._model = spacy.load(model, disable=["ner", "parser"])

    @staticmethod
    def download_spacy_model(model="en_core_web_sm"):
        print(f"Downloading spaCy model {model}")
        spacy.cli.download(model)
        print(f"Finished downloading model")

    @staticmethod
    def load_model(model="en_core_web_sm"):
        return spacy.load(model, disable=["ner", "parser"])

    def preprocess(self, text: str) -> str:

        # Remove Unicode characters otherwise spacy complains
        text = text.encode("ascii", "ignore")
        text = text.decode()
        doc = self._model(text)

        # 1. Tokenize
        tokens = [token for token in doc]

        # 2. Remove numbers
        tokens = [token for token in tokens if not (token.like_num or token.is_currency)]

        # 3. Remove stopwords
        tokens = [token for token in tokens if not token.is_stop]

        # 4. Remove special tokens
        tokens = [token for token in tokens if
                  not (token.is_punct or token.is_space or token.is_quote or token.is_bracket)]
        tokens = [token for token in tokens if token.text.strip() != ""]

        # 5. Lemmatization
        text = " ".join([token.lemma_ for token in tokens])

        # 6. Remove non-alphabetic characters
        text = re.sub(r"[^a-zA-Z\']", " ", text)

        # 7. Remove non-Unicode characters
        text = re.sub(r"[^\x00-\x7F]+", "", text)

        # . 8. Remove punctuation
        text = text.translate(str.maketrans('', '', string.punctuation))

        # 9. Lowercase
        text = text.lower()

        return text


def aspect_link_examples(json_file: str) -> AspectLinkExample:
    """
    Reads the JSON-L file in gzip format.
    Generates an AspectLinkExample in a lazy way (using yield).
    :param json_file: JSON-L file in gzip format.
    """
    with gzip.open(json_file, 'rt', encoding='UTF-8') as zipfile:
        for line in zipfile:
            example = Pykson().from_json(line, AspectLinkExample)
            yield example


def get_entity_ids_only(entities) -> List[str]:
    return [entity.entity_id for entity in entities]


def get_entities(candidate_aspects: List[Aspect], true_aspect: str) -> Tuple[Set[str], Set[str]]:
    pos_entities: Set[str] = set()
    neg_entities: List[str] = []
    for aspect in candidate_aspects:
        if aspect.aspect_id == true_aspect:
            pos_entities = set(get_entity_ids_only(aspect.aspect_content.entities))
        else:
            neg_entities.extend(get_entity_ids_only(aspect.aspect_content.entities))
    return pos_entities, set(neg_entities)


def write_to_file(data: List[str], output_file: str):
    with open(output_file, 'a') as f:
        for line in data:
            f.write("%s\n" % line)


def read_entity_data_file(file_path: str) -> Dict[str, Dict[str, str]]:
    res: Dict[str, Dict[str, str]] = {}
    with open(file_path, 'r') as file:
        for line in file:
            line_parts = line.split("\t")
            if len(line_parts) == 3:
                query_id: str = line_parts[0]
                entity_id: str = line_parts[1]
                if query_id not in res:
                    res[query_id] = {}
                doc: str = line_parts[2]
                res[query_id][entity_id] = doc

    return res


@contextlib.contextmanager
def tqdm_joblib(tqdm_object):
    """
    Context manager to patch joblib to report into tqdm progress bar given as argument
    """

    class TqdmBatchCompletionCallback(joblib.parallel.BatchCompletionCallBack):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

        def __call__(self, *args, **kwargs):
            tqdm_object.update(n=self.batch_size)
            return super().__call__(*args, **kwargs)

    old_batch_callback = joblib.parallel.BatchCompletionCallBack
    joblib.parallel.BatchCompletionCallBack = TqdmBatchCompletionCallback
    try:
        yield tqdm_object
    finally:
        joblib.parallel.BatchCompletionCallBack = old_batch_callback
        tqdm_object.close()
