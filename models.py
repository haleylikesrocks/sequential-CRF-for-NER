# models.py

from optimizers import *
from nerdata import *
from utils import *

import random
import time

from collections import Counter
from typing import List

import numpy as np
from scipy.special import logsumexp


class ProbabilisticSequenceScorer(object):
    """
    Scoring function for sequence models based on conditional probabilities.
    Scores are provided for three potentials in the model: initial scores (applied to the first tag),
    emissions, and transitions. Note that CRFs typically don't use potentials of the first type.

    Attributes:
        tag_indexer: Indexer mapping BIO tags to indices. Useful for dynamic programming
        word_indexer: Indexer mapping words to indices in the emission probabilities matrix
        init_log_probs: [num_tags]-length array containing initial sequence log probabilities
        transition_log_probs: [num_tags, num_tags] matrix containing transition log probabilities (prev, curr)
        emission_log_probs: [num_tags, num_words] matrix containing emission log probabilities (tag, word)
    """
    def __init__(self, tag_indexer: Indexer, word_indexer: Indexer, init_log_probs: np.ndarray, transition_log_probs: np.ndarray, emission_log_probs: np.ndarray):
        self.tag_indexer = tag_indexer
        self.word_indexer = word_indexer
        self.init_log_probs = init_log_probs
        self.transition_log_probs = transition_log_probs
        self.emission_log_probs = emission_log_probs

    def score_init(self, sentence_tokens: List[Token], tag_idx: int):
        return self.init_log_probs[tag_idx]

    def score_transition(self, sentence_tokens: List[Token], prev_tag_idx: int, curr_tag_idx: int):
        return self.transition_log_probs[prev_tag_idx, curr_tag_idx]

    def score_emission(self, sentence_tokens: List[Token], tag_idx: int, word_posn: int):
        word = sentence_tokens[word_posn].word
        word_idx = self.word_indexer.index_of(word) if self.word_indexer.contains(word) else self.word_indexer.index_of("UNK")
        return self.emission_log_probs[tag_idx, word_idx]


class HmmNerModel(object):
    """
    HMM NER model for predicting tags

    Attributes:
        tag_indexer: Indexer mapping BIO tags to indices. Useful for dynamic programming
        word_indexer: Indexer mapping words to indices in the emission probabilities matrix
        init_log_probs: [num_tags]-length array containing initial sequence log probabilities
        transition_log_probs: [num_tags, num_tags] matrix containing transition log probabilities (prev, curr)
        emission_log_probs: [num_tags, num_words] matrix containing emission log probabilities (tag, word)
    """
    def __init__(self, tag_indexer: Indexer, word_indexer: Indexer, init_log_probs, transition_log_probs, emission_log_probs):
        self.tag_indexer = tag_indexer
        self.word_indexer = word_indexer
        self.init_log_probs = init_log_probs
        self.transition_log_probs = transition_log_probs
        self.emission_log_probs = emission_log_probs

    def decode(self, sentence_tokens: List[Token]) -> LabeledSentence:
        """
        See BadNerModel for an example implementation
        :param sentence_tokens: List of the tokens in the sentence to tag
        :return: The LabeledSentence consisting of predictions over the sentence
        """
        pred_tags = []
        num_tags = len(self.init_log_probs)
        matrix = np.zeros((num_tags, len(sentence_tokens)))
        prev = np.zeros((num_tags, len(sentence_tokens) - 1))

        for token in range(len(sentence_tokens)):
            # get word index
            word_index = self.word_indexer.index_of(sentence_tokens[token].word)
            if word_index == -1:
                word_index = self.word_indexer.index_of("UNK")
            # initial probalities
            if token == 0:
                for i in range(num_tags):
                    matrix[i][0] = self.init_log_probs[i] + self.emission_log_probs[i][word_index]
            # subsequent
            else:
                for current_i in range(num_tags):
                    tags_for_i = [matrix[prev_i, token-1] + self.transition_log_probs[prev_i, current_i] for prev_i in range(num_tags)]
                    matrix[current_i][token] = tags_for_i[np.argmax(tags_for_i)] + self.emission_log_probs[current_i, word_index]
                    prev[current_i, token-1] = np.argmax(tags_for_i)
 
        # finding the best sentence through back pass
        last_sate = np.argmax(matrix, 0)[-1]
        best_indices = np.zeros(len(sentence_tokens))
        best_indices[0] = last_sate

        back_track = 1
        for i in range(len(sentence_tokens) - 2, -1, -1):
            best_indices[back_track] = prev[int(last_sate), i]
            last_sate = prev[int(last_sate), i]
            back_track += 1

        best_indices =  np.flip(best_indices, 0)

        # # convert index to tag
        for tag in best_indices:
            pred_tags.append(self.tag_indexer.get_object(tag))

        return LabeledSentence(sentence_tokens, chunks_from_bio_tag_seq(pred_tags))


def train_hmm_model(sentences: List[LabeledSentence], silent: bool=False) -> HmmNerModel:
    """
    Uses maximum-likelihood estimation to read an HMM off of a corpus of sentences.
    Any word that only appears once in the corpus is replaced with UNK. A small amount
    of additive smoothing is applied.
    :param sentences: training corpus of LabeledSentence objects
    :return: trained HmmNerModel
    """
    # Index words and tags. We do this in advance so we know how big our
    # matrices need to be.
    tag_indexer = Indexer()
    word_indexer = Indexer()
    word_indexer.add_and_get_index("UNK")
    word_counter = Counter()
    for sentence in sentences:
        for token in sentence.tokens:
            word_counter[token.word] += 1.0
    for sentence in sentences:
        for token in sentence.tokens:
            # If the word occurs fewer than two times, don't index it -- we'll treat it as UNK
            get_word_index(word_indexer, word_counter, token.word)
        for tag in sentence.get_bio_tags():
            tag_indexer.add_and_get_index(tag)
    # Count occurrences of initial tags, transitions, and emissions
    # Apply additive smoothing to avoid log(0) / infinities / etc.
    init_counts = np.ones((len(tag_indexer)), dtype=float) * 0.001
    transition_counts = np.ones((len(tag_indexer),len(tag_indexer)), dtype=float) * 0.001
    emission_counts = np.ones((len(tag_indexer),len(word_indexer)), dtype=float) * 0.001
    for sentence in sentences:
        bio_tags = sentence.get_bio_tags()
        for i in range(0, len(sentence)):
            tag_idx = tag_indexer.add_and_get_index(bio_tags[i])
            word_idx = get_word_index(word_indexer, word_counter, sentence.tokens[i].word)
            emission_counts[tag_idx][word_idx] += 1.0
            if i == 0:
                init_counts[tag_idx] += 1.0
            else:
                transition_counts[tag_indexer.add_and_get_index(bio_tags[i-1])][tag_idx] += 1.0
    # Turn counts into probabilities for initial tags, transitions, and emissions. All
    # probabilities are stored as log probabilities
    if not silent:
        print(repr(init_counts))
    init_counts = np.log(init_counts / init_counts.sum())
    # transitio`ns are stored as count[prev state][next state], so we sum over the second axis
    # and normalize by that to get the right conditional probabilities
    transition_counts = np.log(transition_counts / transition_counts.sum(axis=1)[:, np.newaxis])
    # similar to transitions
    emission_counts = np.log(emission_counts / emission_counts.sum(axis=1)[:, np.newaxis])
    if not silent:
        print("Tag indexer: %s" % tag_indexer)
        print("Initial state log probabilities: %s" % init_counts)
        print("Transition log probabilities: %s" % transition_counts)
        print("Emission log probs too big to print...")
        print("Emission log probs for India: %s" % emission_counts[:,word_indexer.add_and_get_index("India")])
        print("Emission log probs for Phil: %s" % emission_counts[:,word_indexer.add_and_get_index("Phil")])
        print("   note that these distributions don't normalize because it's p(word|tag) that normalizes, not p(tag|word)")
    return HmmNerModel(tag_indexer, word_indexer, init_counts, transition_counts, emission_counts)


def get_word_index(word_indexer: Indexer, word_counter: Counter, word: str) -> int:
    """
    Retrieves a word's index based on its count. If the word occurs only once, treat it as an "UNK" token
    At test time, unknown words will be replaced by UNKs.
    :param word_indexer: Indexer mapping words to indices for HMM featurization
    :param word_counter: Counter containing word counts of training set
    :param word: string word
    :return: int of the word index
    """
    if word_counter[word] < 1.5:
        return word_indexer.add_and_get_index("UNK")
    else:
        return word_indexer.add_and_get_index(word)


##################
# CRF code follows

class FeatureBasedSequenceScorer(object):
    """
    Feature-based sequence scoring model. Note that this scorer is instantiated *for every example*: it contains
    the feature cache used for that example.
    """
    def __init__(self, tag_indexer, feature_weights, feat_cache):
        self.tag_indexer = tag_indexer
        self.feature_weights = feature_weights
        self.feat_cache = feat_cache

    def score_init(self, sentence, tag_idx):
        if isI(self.tag_indexer.get_object(tag_idx)):
            return -1000
        else:
            return 0

    def score_transition(self, sentence_tokens, prev_tag_idx, curr_tag_idx):
        prev_tag = self.tag_indexer.get_object(prev_tag_idx)
        curr_tag = self.tag_indexer.get_object(curr_tag_idx)
        if (isO(prev_tag) and isI(curr_tag))\
                or (isB(prev_tag) and isI(curr_tag) and get_tag_label(prev_tag) != get_tag_label(curr_tag)) \
                or (isI(prev_tag) and isI(curr_tag) and get_tag_label(prev_tag) != get_tag_label(curr_tag)):
            return -1000
        else:
            return 0

    def score_emission(self, sentence_tokens, tag_idx, word_posn):
        feats = self.feat_cache[word_posn][tag_idx]
        return self.feature_weights.score(feats) # feature * weigth # this is the isssue hear


class CrfNerModel(object):
    def __init__(self, tag_indexer, feature_indexer, feature_weights):
        self.tag_indexer = tag_indexer
        self.feature_indexer = feature_indexer
        self.feature_weights = feature_weights

    def decode(self, sentence_tokens: List[Token]) -> LabeledSentence:
        """
        See BadNerModel for an example implementation
        :param sentence_tokens: List of the tokens in the sentence to tag
        :return: The LabeledSentence consisting of predictions over the sentence
        """
        #calculate feature chache
        feature_cache = [[[] for k in range(0, len(self.tag_indexer))] for i in range(0, len(sentence_tokens))]
        for word_idx in range(0, len(sentence_tokens)):
            for tag_idx in range(0, len(self.tag_indexer)):
                feature_cache[word_idx][tag_idx] = extract_emission_features(sentence_tokens, word_idx, self.tag_indexer.get_object(tag_idx), self.feature_indexer, add_to_indexer=False)
        scorer = FeatureBasedSequenceScorer(self.tag_indexer, self.feature_weights, feature_cache)

        pred_tags = []
        num_tags = len(self.tag_indexer)
        matrix = np.zeros((num_tags, len(sentence_tokens)))
        prev = np.zeros((num_tags, len(sentence_tokens) - 1))

        for token in range(len(sentence_tokens)):
            # initial probalities
            if token == 0:
                for i in range(num_tags):
                    matrix[i][0] = scorer.score_init(sentence_tokens, i) + scorer.score_emission(sentence_tokens, i, token)
            # subsequent
            else:
                for current_i in range(num_tags):
                    tags_for_i = [matrix[prev_i, token-1] + scorer.score_transition(sentence_tokens, prev_i, current_i) for prev_i in range(num_tags)]
                    matrix[current_i][token] = tags_for_i[np.argmax(tags_for_i)] + scorer.score_emission(sentence_tokens, current_i, token) # what to do if word no good
                    prev[current_i, token-1] = np.argmax(tags_for_i)

        # finding the best sentence through back pass
        last_sate = np.argmax(matrix, 0)[-1]
        best_indices = np.zeros(len(sentence_tokens))
        best_indices[0] = last_sate

        back_track = 1
        for i in range(len(sentence_tokens) - 2, -1, -1):
            best_indices[back_track] = prev[int(last_sate), i]
            last_sate = prev[int(last_sate), i]
            back_track += 1

        best_indices =  np.flip(best_indices, 0)

        # # convert index to tag
        for tag in best_indices:
            pred_tags.append(self.tag_indexer.get_object(tag))

        return LabeledSentence(sentence_tokens, chunks_from_bio_tag_seq(pred_tags))


    def decode_beam(self, sentence_tokens: List[Token]) -> LabeledSentence:
        """
        See BadNerModel for an example implementation
        :param sentence_tokens: List of the tokens in the sentence to tag
        :return: The LabeledSentence consisting of predictions over the sentence
        """
        beam_size = 9
        pred_tags = []
        num_words = len(sentence_tokens)
        num_tags = len(self.tag_indexer)
        current_beam = Beam(beam_size)
        next_beam = Beam(beam_size)

        #calculate feature chache
        feature_cache = [[[] for k in range(0, num_tags)] for i in range(0, num_words)]
        for word_idx in range(0, num_words):
            for tag_idx in range(0, num_tags):
                feature_cache[word_idx][tag_idx] = extract_emission_features(sentence_tokens, word_idx, self.tag_indexer.get_object(tag_idx), self.feature_indexer, add_to_indexer=False)
        scorer = FeatureBasedSequenceScorer(self.tag_indexer, self.feature_weights, feature_cache)
        
        # Intialize Beam 
        for i in range(num_tags):
            current_beam.add([i], scorer.score_init(sentence_tokens, i) + scorer.score_emission(sentence_tokens, i, 0))

        for token in range(1, num_words):
            tags, scores = zip(*list(current_beam.get_elts_and_scores()))
            for i in range(beam_size):
                for current_i in range(num_tags):
                    seq = tags[i] + [current_i]
                    next_beam.add(seq, scores[i] + scorer.score_transition(sentence_tokens, tags[i][-1], current_i) + scorer.score_emission(sentence_tokens, current_i, token))
            current_beam = next_beam

        # convert Index to Tag
        last_state = current_beam.head()
        for tag in last_state:
            pred_tags.append(self.tag_indexer.get_object(tag))
        return LabeledSentence(sentence_tokens, chunks_from_bio_tag_seq(pred_tags))


def train_crf_model(sentences: List[LabeledSentence], silent: bool=False) -> CrfNerModel:
    """
    Trains a CRF NER model on the given corpus of sentences.
    :param sentences: The training data
    :param silent: True to suppress output, false to print certain debugging outputs
    :return: The CrfNerModel, which is primarily a wrapper around the tag + feature indexers as well as weights
    """
    tag_indexer = Indexer()
    for sentence in sentences:
        for tag in sentence.get_bio_tags():
            tag_indexer.add_and_get_index(tag)
    if not silent:
        print("Extracting features")
    feature_indexer = Indexer()
    # 4-d list indexed by sentence index, word index, tag index, feature index
    feature_cache = [[[[] for k in range(0, len(tag_indexer))] for j in range(0, len(sentences[i]))] for i in range(0, len(sentences))]
    for sentence_idx in range(0, len(sentences)):
        if sentence_idx % 100 == 0 and not silent:
            print("Ex %i/%i" % (sentence_idx, len(sentences)))
        for word_idx in range(0, len(sentences[sentence_idx])):
            for tag_idx in range(0, len(tag_indexer)):
                feature_cache[sentence_idx][word_idx][tag_idx] = extract_emission_features(sentences[sentence_idx].tokens, word_idx, tag_indexer.get_object(tag_idx), feature_indexer, add_to_indexer=True)
    if not silent:
        print("Training")
    weight_vector = UnregularizedAdagradTrainer(np.zeros((len(feature_indexer))), eta=1.0)
    num_epochs = 3
    random.seed(0)
    for epoch in range(0, num_epochs):
        epoch_start = time.time()
        if not silent:
            print("Epoch %i" % epoch)
        sent_indices = [i for i in range(0, len(sentences))]
        random.shuffle(sent_indices)
        total_obj = 0.0
        for counter, i in enumerate(sent_indices):
            if counter % 100 == 0 and not silent:
                print("Ex %i/%i" % (counter, len(sentences)))
            scorer = FeatureBasedSequenceScorer(tag_indexer, weight_vector, feature_cache[i])
            (gold_log_prob, gradient) = compute_gradient(sentences[i], tag_indexer, scorer, feature_indexer)
            total_obj += gold_log_prob
            weight_vector.apply_gradient_update(gradient, 1)
        if not silent:
            print("Objective for epoch: %.2f in time %.2f" % (total_obj, time.time() - epoch_start))
    return CrfNerModel(tag_indexer, feature_indexer, weight_vector)


def extract_emission_features(sentence_tokens: List[Token], word_index: int, tag: str, feature_indexer: Indexer, add_to_indexer: bool):
    """
    Extracts emission features for tagging the word at word_index with tag.
    :param sentence_tokens: sentence to extract over
    :param word_index: word index to consider
    :param tag: the tag that we're featurizing for
    :param feature_indexer: Indexer over features
    :param add_to_indexer: boolean variable indicating whether we should be expanding the indexer or not. This should
    be True at train time (since we want to learn weights for all features) and False at test time (to avoid creating
    any features we don't have weights for).
    :return: an ndarray
    """
    feats = []
    curr_word = sentence_tokens[word_index].word
    # Lexical and POS features on this word, the previous, and the next (Word-1, Word0, Word1)
    for idx_offset in range(-1, 2):
        if word_index + idx_offset < 0:
            active_word = "<s>"
        elif word_index + idx_offset >= len(sentence_tokens):
            active_word = "</s>"
        else:
            active_word = sentence_tokens[word_index + idx_offset].word
        if word_index + idx_offset < 0:
            active_pos = "<S>"
        elif word_index + idx_offset >= len(sentence_tokens):
            active_pos = "</S>"
        else:
            active_pos = sentence_tokens[word_index + idx_offset].pos
        maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":Word" + repr(idx_offset) + "=" + active_word)
        maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":Pos" + repr(idx_offset) + "=" + active_pos)
    # Character n-grams of the current word
    max_ngram_size = 3
    for ngram_size in range(1, max_ngram_size+1):
        start_ngram = curr_word[0:min(ngram_size, len(curr_word))]
        maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":StartNgram=" + start_ngram)
        end_ngram = curr_word[max(0, len(curr_word) - ngram_size):]
        maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":EndNgram=" + end_ngram)
    # Look at a few word shape features
    maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":IsCap=" + repr(curr_word[0].isupper()))
    # Compute word shape
    new_word = []
    for i in range(0, len(curr_word)):
        if curr_word[i].isupper():
            new_word += "X"
        elif curr_word[i].islower():
            new_word += "x"
        elif curr_word[i].isdigit():
            new_word += "0"
        else:
            new_word += "?"
    maybe_add_feature(feats, feature_indexer, add_to_indexer, tag + ":WordShape=" + repr(new_word))
    return np.asarray(feats, dtype=int)

 
def compute_gradient(sentence: LabeledSentence, tag_indexer: Indexer, scorer: FeatureBasedSequenceScorer, feature_indexer: Indexer):# -> (float, Counter):
    """
    Computes the gradient of the given example (sentence). The bulk of this code will be computing marginals via
    forward-backward: you should first compute these marginals, then accumulate the gradient based on the log
    probabilities.
    :param sentence: The LabeledSentence of the current example
    :param tag_indexer: The Indexer of the tags
    :param scorer: FeatureBasedSequenceScorer is a scoring model that wraps the weight vector and which also contains a
    feat_cache field that will be useful when computing the gradient.
    :param feature_indexer: The Indexer of the features
    :return: A tuple of two items. The first is the log probability of the correct sequence, which corresponds to the
    training objective. This value is only needed for printing, so technically you do not *need* to return it, but it
    will probably be useful to compute for debugging purposes.
    The second value is a Counter containing the gradient -- this is a sparse map from indices (features)
    to weights (gradient values).
    """
    # take sum of gold features over i
    full_feat = np.array([])
    features = {} #marginal tags should sum to zero
    num_tags = len(tag_indexer)
    num_words = len(sentence)
    marginals = np.zeros((num_tags, num_words))
    alpha_matrix = np.zeros((num_tags, num_words))
    beta_matrix = np.zeros((num_tags, num_words))
    
    # calculate the marginal prob using forward back ward
    # initialize Matracise
    for i in range(num_tags):
        alpha_matrix[i][0] = scorer.score_emission(sentence, i, 0)
        beta_matrix[i][num_words-1] = 0.0
    # Foward pass
    for idx in range(1, num_words):
        for current_i in range(num_tags):
            tags_for_i = [alpha_matrix[prev_i, idx-1] + scorer.score_transition(sentence, prev_i, current_i) + (scorer.score_emission(sentence, current_i, idx) )for prev_i in range(num_tags)]
            alpha_matrix[current_i][idx] = logsumexp(tags_for_i, 0)
    #backward pass
    for idx in range(num_words - 2, -1, -1):
        for current_i in range(num_tags):
            beta_matrix[current_i][idx] = logsumexp([beta_matrix[next_i, idx + 1] + scorer.score_transition(sentence, current_i, next_i) + (scorer.score_emission(sentence, next_i, idx + 1) )for next_i in range(num_tags)], 0)

    # calcualte gold and expected emssion features
    denominators = logsumexp(np.add(alpha_matrix, beta_matrix), 0)
    # print(denominators) #check for normilization
    for word_idx in range(len(sentence)):
        gold_tag_index = tag_indexer.index_of(sentence.get_bio_tags()[word_idx])
        full_feat = np.append(full_feat, scorer.feat_cache[word_idx][gold_tag_index])

        for tag_idx in range(num_tags):
            marginals[tag_idx][word_idx] = np.exp(alpha_matrix[tag_idx][word_idx] + beta_matrix[tag_idx][word_idx] - denominators[word_idx])
            
            for feature in scorer.feat_cache[word_idx][tag_idx]:
                if feature in features:
                    features[feature] += marginals[tag_idx][word_idx] 
                else:
                    features[feature] = marginals[tag_idx][word_idx]
    # print(np.sum(marginals,0)) # marginals check
    
    probs = logsumexp([scorer.score_emission(sentence, tag_indexer.index_of(sentence.get_bio_tags()[i]) ,i) for i in range(len(sentence))])

    marginal = Counter(features)
    gold = Counter(full_feat.astype(int))
    gold.subtract(marginal)

    return  (probs, gold)  # will just change from gold will not assign anything (changes in place)
