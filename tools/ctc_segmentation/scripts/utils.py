# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import logging.handlers
import multiprocessing
import os
from pathlib import PosixPath
from typing import List, Tuple, Union

import ctc_segmentation as cs
import numpy as np
from nemo.collections.common.tokenizers.sentencepiece_tokenizer import SentencePieceTokenizer


def get_segments(
        log_probs: np.ndarray,
        path_wav: Union[PosixPath, str],
        transcript_file: Union[PosixPath, str],
        output_file: str,
        vocabulary: List[str],
        tokenizer: SentencePieceTokenizer,
        asr_model_name: str,
        index_duration: float,
        window_size: int = 8000,
) -> None:
    """
    Segments the audio into segments and saves segments timings to a file

    Args:
        log_probs: Log probabilities for the original audio from an ASR model, shape T * |vocabulary|.
                   values for blank should be at position 0
        path_wav: path to the audio .wav file
        transcript_file: path to
        output_file: path to the file to save timings for segments
        vocabulary: vocabulary used to train the ASR model, note blank is at position len(vocabulary) - 1
        tokenizer: ASR model tokenizer (for BPE models, None for Quartznet)
        asr_model_name: name of the CTC-based ASR model
        window_size: the length of each utterance (in terms of frames of the CTC outputs) fits into that window.
        index_duration: corresponding time duration of one CTC output index (in seconds)
    """
    config = cs.CtcSegmentationParameters()
    config.char_list = vocabulary
    config.min_window_size = window_size
    config.index_duration = index_duration  # 0.0799983368347339
    config.blank = len(vocabulary) - 1
    # config.space = "▁"

    with open(transcript_file, "r") as f:
        text = f.readlines()
        text = [t.strip() for t in text if t.strip()]

    # add corresponding original text without pre-processing
    transcript_file_no_preprocessing = transcript_file.replace('.txt', '_with_punct.txt')
    if not os.path.exists(transcript_file_no_preprocessing):
        raise ValueError(f'{transcript_file_no_preprocessing} not found.')

    with open(transcript_file_no_preprocessing, "r") as f:
        text_no_preprocessing = f.readlines()
        text_no_preprocessing = [t.strip() for t in text_no_preprocessing if t.strip()]

    # add corresponding normalized original text
    transcript_file_normalized = transcript_file.replace('.txt', '_with_punct_normalized.txt')
    if not os.path.exists(transcript_file_normalized):
        raise ValueError(f'{transcript_file_normalized} not found.')

    with open(transcript_file_normalized, "r") as f:
        text_normalized = f.readlines()
        text_normalized = [t.strip() for t in text_normalized if t.strip()]

    if len(text_no_preprocessing) != len(text):
        raise ValueError(f'{transcript_file} and {transcript_file_no_preprocessing} do not match')

    if len(text_normalized) != len(text):
        raise ValueError(f'{transcript_file} and {transcript_file_normalized} do not match')

    # works for sentences CitriNet
    from prepare_bpe import prepare_tokenized_text_nemo_works_modified
    ground_truth_mat, utt_begin_indices = prepare_tokenized_text_nemo_works_modified(text, tokenizer, vocabulary)
    # _print(ground_truth_mat, vocabulary)

    """
    # QN
    from prepare_bpe import prepare_text_default, get_config_qn
    config = get_config_qn()
    config.min_window_size = window_size
    config.index_duration = index_duration 
    ground_truth_mat, utt_begin_indices = prepare_text_default(config, text)
    _print(ground_truth_mat, config.char_list)
    """

    logging.debug(f"Syncing {transcript_file}")
    logging.debug(
        f"Audio length {os.path.basename(path_wav)}: {log_probs.shape[0]}. "
        f"Text length {os.path.basename(transcript_file)}: {len(ground_truth_mat)}"
    )

    try:
        timings, char_probs, char_list = cs.ctc_segmentation(config, log_probs, ground_truth_mat)
        segments = cs.determine_utterance_segments(config, utt_begin_indices, char_probs, timings, text)

        """
        # WIP to split long audio segments after initial segmentation
        # extract char_probs for segment of interest
        seg_id = 3
        seg_id_start = utt_begin_indices[seg_id] - 1
        seg_id_end = utt_begin_indices[seg_id + 1]
        start = _compute_time(seg_id_start, "begin", timings)
        end = _compute_time(seg_id_end, "end", timings)
        start_t = int(round(start / config.index_duration_in_seconds))
        end_t = int(round(end / config.index_duration_in_seconds))
        utterance = char_list[start_t: end_t]
        char_probs_seg = char_probs[start_t: end_t]

        text_seg = ["under the protection of a passenger", "and a trusty dog"]
        timings_seg = timings[seg_id_start: seg_id_end]
        utt_begin_indices_seg = [1, 11, timings_seg.shape[0] - 1]
        blank_spans = _get_blank_spans(utterance)
        ground_truth_mat_seg = ground_truth_mat[seg_id_start: seg_id_end]
        # sort by the blank count
        blank_spans = sorted(blank_spans, key=lambda x: x[2], reverse=True)

        segments_short = cs.determine_utterance_segments(config, utt_begin_indices_seg, char_probs, timings_seg, text_seg)
        print(segments_short)
        print()
        print(utterance)
        print(blank_spans)

        # for i, (word, segment) in enumerate(zip(text, segments)):
        #     if i < 10:
        #         print(f"{segment[0]:.2f} {segment[1]:.2f} {segment[2]:3.4f} {word}")


        segments[seg_id] = segments_short
        text[seg_id] = text_seg
        text_normalized[seg_id] = text_seg
        text_no_preprocessing[seg_id] = text_seg
        """

        write_output(output_file, path_wav, segments, text, text_no_preprocessing, text_normalized)

    except Exception as e:
        logging.info(e)
        logging.info(f"segmentation of {transcript_file} failed")

def _compute_time(index, align_type, timings):
    """Compute start and end time of utterance.
    :param index:  frame index value
    :param align_type:  one of ["begin", "end"]
    :return: start/end time of utterance in seconds
    """
    middle = (timings[index] + timings[index - 1]) / 2
    if align_type == "begin":
        return max(timings[index + 1] - 0.5, middle)
    elif align_type == "end":
        return min(timings[index - 1] + 0.5, middle)

def _print(ground_truth_mat, vocabulary):
    chars = []
    for row in ground_truth_mat:
        chars.append([])
        for ch_id in row:
            if ch_id != -1:
                chars[-1].append(vocabulary[int(ch_id)])

    [print(x) for x in chars[:100]]


def _get_blank_spans(char_list, blank='ε'):
    """
    Returns a list of tuples:
        (start index, end index (exclusive), count)

    ignores blank symbols at the beginning and end of the char_list
    since they're not suitable for split in between
    """
    blanks = []
    start = None
    end = None
    for i, ch in enumerate(char_list):
        if ch == blank:
            if start is None:
                start, end = i, i
            else:
                end = i
        else:
            if start is not None:
                # ignore blank tokens at the beginning
                if start > 0:
                    end += 1
                    blanks.append((start, end, end - start))
                start = None
                end = None
    return blanks

def write_output(
        out_path: str,
        path_wav: str,
        segments: List[Tuple[float]],
        text: str,
        text_no_preprocessing: str,
        text_normalized: str
):
    """
    Write the segmentation output to a file

    out_path: Path to output file
    path_wav: Path to the original audio file
    segments: Segments include start, end and alignment score
    text: Text used for alignment
    text_no_preprocessing: Reference txt without any pre-processing
    text_normalized: Reference text normalized
    """
    # Uses char-wise alignments to get utterance-wise alignments and writes them into the given file
    with open(str(out_path), "w") as outfile:
        outfile.write(str(path_wav) + "\n")

        for i, segment in enumerate(segments):
            if isinstance(segment, list):
                for j, x in enumerate(segment):
                    start, end, score = x
                    score = -0.2
                    outfile.write(
                        f'{start} {end} {score} | {text[i][j]} | {text_no_preprocessing[i][j]} | {text_normalized[i][j]}\n'
                    )
            else:
                start, end, score = segment
                outfile.write(
                    f'{start} {end} {score} | {text[i]} | {text_no_preprocessing[i]} | {text_normalized[i]}\n'
                )


#####################
# logging utils
#####################
def listener_configurer(log_file, level):
    root = logging.getLogger()
    h = logging.handlers.RotatingFileHandler(log_file, 'w')
    f = logging.Formatter('%(asctime)s %(processName)-10s %(name)s %(levelname)-8s %(message)s')
    h.setFormatter(f)
    ch = logging.StreamHandler()
    root.addHandler(h)
    root.setLevel(level)
    root.addHandler(ch)


def listener_process(queue, configurer, log_file, level):
    configurer(log_file, level)
    while True:
        try:
            record = queue.get()
            if record is None:  # We send this as a sentinel to tell the listener to quit.
                break
            logger = logging.getLogger(record.name)
            logger.setLevel(logging.INFO)
            logger.handle(record)  # No level or filter logic applied - just do it!

        except Exception:
            import sys
            import traceback

            print('Problem:', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)


def worker_configurer(queue, level):
    h = logging.handlers.QueueHandler(queue)  # Just the one handler needed
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(level)


def worker_process(
        queue, configurer, level, log_probs, path_wav, transcript_file, output_file, vocabulary, tokenizer, asr_model,
        index_duration, window_len
):
    configurer(queue, level)
    name = multiprocessing.current_process().name
    innerlogger = logging.getLogger('worker')
    innerlogger.info(f'{name} is processing {path_wav}, window_len={window_len}')
    get_segments(log_probs, path_wav, transcript_file, output_file, vocabulary, tokenizer, asr_model, index_duration,
                 window_len)
    innerlogger.info(f'{name} completed segmentation of {path_wav}, segments saved to {output_file}')
