import nltk
from nltk.tokenize import word_tokenize
from nltk.tag import StanfordPOSTagger
from tqdm import tqdm
import numpy as np
import pandas as pd
import os
import csv
import sys
import math
from sentence_transformers import SentenceTransformer
from nltk.stem import WordNetLemmatizer

# Refer to https://github.com/UKPLab/sentence-transformers to download Sentence-BERT pre-training model
model = SentenceTransformer('sentence-transformers/bert-base-nli-mean-tokens')

# Refer to https://nlp.stanford.edu/software/tagger.shtml to download the tagging model
eng_tag = StanfordPOSTagger(model_filename='corenlp/postagger/models/english-left3words-distsim.tagger', \
                          path_to_jar='corenlp/postagger/stanford-postagger-3.9.2.jar')

# verb(v), adjective(a), adverb(r), noun(n), others(u)
pos_tag_ids_map = {'v':0, 'a':1, 'r':2, 'n':3, 'u':4}

lemmatizer = WordNetLemmatizer()

# Map the words' POS tag, got from using eng_tag(StanfordPOSTagger), to one of 5 legal POS tags in this project
def convert_postag(pos):
    """Convert NLTK POS tags to SentiWordNet's POS tags."""
    if pos in ['VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']:
        return 'v'
    elif pos in ['JJ', 'JJR', 'JJS']:
        return 'a'
    elif pos in ['RB', 'RBR', 'RBS']:
        return 'r'
    elif pos in ['NNS', 'NN', 'NNP', 'NNPS']:
        return 'n'
    else:
        return 'u'


def write(filename, data):
    with open(filename, 'w') as f:
        for line in data:
            f.write(str(line) + '\n')

def load_sentinet(senti_file_name, gloss_file_name):
    # load sentiwordnet
    f = open(senti_file_name, 'r')

    line_id = 0
    sentinet = {}

    for line in f.readlines():
        if line_id < 26:
            line_id += 1
            continue
        if line_id == 26:
            print(line)
        if line_id == 117685:
            print(line)
            break
        line_split = line.strip().split('\t')
        pos, pscore, nscore, term, gloss = line_split[0], float(line_split[2]), float(line_split[3]), line_split[4], \
                                           line_split[5]

        if "\"" in gloss:
            shop_pos = gloss.index('\"')
            gloss = gloss[: shop_pos - 2]
        each_term = term.split(' ')
        for ele in each_term:
            ele_split = ele.split('#')
            assert len(ele_split) == 2
            word, sn = ele_split[0], int(ele_split[1])
            if word not in sentinet:
                sentinet[word] = {}
            if pos not in sentinet[word]:
                sentinet[word][pos] = []
            sentinet[word][pos].append([sn, pscore, nscore, gloss, line_id - 26])
        line_id += 1

    f.close()

    # load gloss embedding (which is calculated by sentence-transformers in advance)
    gloss_embedding = np.load(gloss_file_name)
    gloss_emb_norm = [np.linalg.norm(gloss_embedding[id]) for id in range(len(gloss_embedding))]
    gloss_emb_norm = np.array(gloss_emb_norm)

    return sentinet, gloss_embedding, gloss_emb_norm

# Calculate the similarity between input texts and gloss
def cos_sim(a, b, norm_a, norm_b):
    dot_prod = np.dot(a,b)
    return dot_prod / (norm_a * norm_b)

# Get POS tags and Polarities for each word
def process_text(text_list, label_list, sentinet, gloss_embedding, gloss_emb_norm):
    sent_list = []
    sent_list_str = []
    data_cnt = 0

    # Tokenization with NLTK
    for text in text_list:
        try:
            token_list = word_tokenize(text.strip())
        except:
            token_list = text.strip().split()
        if len(token_list) == 0:
            continue
        sent_list.append(token_list)
        sent_list_str.append(text.strip())
        data_cnt += 1

    print('original number of data = ', data_cnt)

    # pos tagging with Stanford Corenlp
    sent_split = eng_tag.tag_sents(sent_list)

    # sentence embedding
    corpus_embedding = model.encode(sent_list_str, batch_size=64) # convert text -> vector (768,)
    corpus_embedding = np.array(corpus_embedding)
    corpus_emb_norm = [np.linalg.norm(corpus_embedding[id]) for id in range(len(corpus_embedding))] # vector (768,) -> 1 real number
    corpus_emb_norm = np.array(corpus_emb_norm)
    assert len(corpus_embedding) == len(sent_split)

    # get pos tags and sentiment polarities for each word
    clean_sent_list, pos_list, senti_list, clean_label_list = [], [], [], []
    for sent_id in range(len(sent_split)):
        sent_list_ele, pos_list_ele, senti_list_ele = [], [], []
        for pair in sent_split[sent_id]:
            if len(pair[0]) != 0:
                word, pos = pair[0], convert_postag(pair[1])
                sent_list_ele.append(word)
                pos_list_ele.append(pos)
                if pos != 'u':
                    word = lemmatizer.lemmatize(word.lower(), pos=pos)

                # gloss-aware sentiment attention
                if word in sentinet and pos in sentinet[word]:
                    sim_list = []
                    score_list = []
                    for ele_term in sentinet[word][pos]:
                        gloss_line = ele_term[4]
                        gloss_emb, gloss_norm = gloss_embedding[gloss_line], gloss_emb_norm[gloss_line]
                        sent_emb, sent_norm = corpus_embedding[sent_id], corpus_emb_norm[sent_id]
                        sim_score = cos_sim(gloss_emb, sent_emb, gloss_norm, sent_norm)
                        sim_list.append((sim_score + 1) / (2 * ele_term[0]))
                        score_list.append(ele_term[1] - ele_term[2]) # pos-score - neg-score

                    # Calculate the sentiment polarity for each word in a text
                    sim_exp = [math.exp(sim_list[id]) for id in range(len(sim_list))] # math.exp(x) = e^x
                    sum_sim_exp = sum(sim_exp)
                    sim_exp = np.array([sim_exp[id] / sum_sim_exp for id in range(len(sim_exp))])
                    score_list = np.array(score_list)
                    final_score = np.dot(sim_exp, score_list)
                    senti_list_ele.append(final_score)
                else:
                    senti_list_ele.append(0.0)

        assert len(sent_list_ele) == len(pos_list_ele)
        assert len(sent_list_ele) == len(senti_list_ele)

        if len(sent_list) != 0:
            clean_sent_list.append(sent_list_ele)
            # transform pos_tag (str) to integer
            pos_list.append([pos_tag_ids_map[ele] for ele in pos_list_ele])
            # transform sentiment score (float) to integer
            senti_list.append([1 if ele > 0 else 0 if ele < 0 else 2 for ele in senti_list_ele])
            clean_label_list.append(label_list[sent_id])

    assert len(clean_sent_list) == len(clean_label_list)
    assert len(clean_sent_list) == len(pos_list)
    assert len(clean_sent_list) == len(senti_list)

    print('number after processing = ', len(clean_label_list))

    return clean_sent_list, pos_list, senti_list, clean_label_list


def read_tsv(input_file, quotechar=None):
    """Reads a tab separated value file."""
    with open(input_file, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
        lines = []
        for line in reader:
            if sys.version_info[0] == 2:
                line = list(unicode(cell, 'utf-8') for cell in line)
            lines.append(line)
        return lines


def convert_sentence(path, set_name, sentinet, gloss_embedding, gloss_embedding_norm):
    data = read_tsv(path + set_name + ".tsv")
    text_list, label_list = [], []
    for x in tqdm(data[1:]):    # remove the title line
        text_list.append(x[0])  # get all input texts
        label_list.append(int(x[1]))    # label of texts (1,2,3,4,5)

    # Get POS tag and Polarity for each word
    clean_text_list, pos_list, senti_list, clean_label_list = process_text(text_list, label_list, sentinet, gloss_embedding, gloss_embedding_norm)

    # Write preprocessed data to new file
    text_pos_senti_list = []
    for data_id in range(len(clean_text_list)):
        text_pos_senti_list.append([clean_text_list[data_id], pos_list[data_id], senti_list[data_id], clean_label_list[data_id]])
    write(path + set_name + "_newpos.txt", text_pos_senti_list)


# In this python file, the authors just preprocess in 3 datasets which are MR, SST, IMDB
# but the main datasets used for not only pre-training but also the whole project are already preprocessed Yelp data files
path_set = ["raw_data/sent/mr/", "raw_data/sent/sst/", "raw_data/sent/imdb/"]
set_name = ["train", "dev", "test"]

# Refer to https://github.com/aesuli/SentiWordNet to download SentiWordNet 3.0
sentinet, gloss_embedding, gloss_embedding_norm = load_sentinet('SentiWordNet_3.0.0.txt', 'gloss_embedding.npy')
# sentinet: a dictionary contains:
#   dictionary of words with their POS
#       each POS of a word has a list(s) indexs likes [sn, pos-score, neg-score, gloss, line_id-26]
#
# gloss_embedding.npy stores 117659 embedding vectors obtained by Sentence-BERT for each gloss

for path in path_set:
    for task in set_name:
        convert_sentence(path, task, sentinet, gloss_embedding, gloss_embedding_norm)
