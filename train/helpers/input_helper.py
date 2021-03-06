#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Utility to handle inputs."""

import tensorflow as tf
import tensorflow.contrib.data as data


class BatchedInput(object):
    def __init__(self, initializer=None, source=None, target_input=None,
                 target_output=None, source_sequence_length=None,
                 target_sequence_length=None):
        self.initializer = initializer
        self.source = source
        self.target_input = target_input
        self.target_output = target_output
        self.source_sequence_length = source_sequence_length
        self.target_sequence_length = target_sequence_length


# an epoch
def get_infer_input(hparams, src_file, src_vocab_table):
    src_eos_id = tf.cast(src_vocab_table.lookup(tf.constant(hparams.eos)), tf.int32)
    src_dataset = data.TextLineDataset(src_file)
    src_dataset = src_dataset.map(lambda src: tf.string_split([src]).values)

    if hparams.src_max_len:
        src_dataset = src_dataset.map(lambda src: src[:hparams.src_max_len])
        # Convert the word strings to ids
    src_dataset = src_dataset.map(
        lambda src: tf.cast(src_vocab_table.lookup(src), tf.int32))
    if hparams.source_reverse:
        src_dataset = src_dataset.map(lambda src: tf.reverse(src, axis=[0]))
    # Add in the word counts.
    src_dataset = src_dataset.map(lambda src: (src, tf.size(src)))

    def batching_func(x):
        return x.padded_batch(
            hparams.infer_batch_size,
            # The entry is the source line rows;
            # this has unknown-length vectors.  The last entry is
            # the source row size; this is a scalar.
            padded_shapes=(tf.TensorShape([None]),  # src
                            tf.TensorShape([])),     # src_len
            # Pad the source sequences with hparams.eos tokens.
            # (Though notice we don't generally need to do this since
            # later on we will be masking out calculations past the true sequence.
            padding_values=(src_eos_id,  # src
                            0))          # src_len -- unused

    batched_dataset = batching_func(src_dataset)
    batched_iter = batched_dataset.make_initializable_iterator()
    (src_ids, src_seq_len) = batched_iter.get_next()
    return BatchedInput(
        initializer=batched_iter.initializer,
        source=src_ids,
        target_input=None,
        target_output=None,
        source_sequence_length=src_seq_len,
        target_sequence_length=None)


# an epoch
def get_input(hparams,
              mode,
              src_file,
              tgt_file,
              src_vocab_table,
              tgt_vocab_table,
              num_threads=4,
              output_buffer_size=None,
              skip_count=None):
    batch_size = hparams.batch_size
    if not output_buffer_size:
        output_buffer_size = batch_size * 1000
    src_eos_id = tf.cast(
        src_vocab_table.lookup(tf.constant(hparams.eos)),
        tf.int32)
    tgt_sos_id = tf.cast(
        tgt_vocab_table.lookup(tf.constant(hparams.sos)),
        tf.int32)
    tgt_eos_id = tf.cast(
        tgt_vocab_table.lookup(tf.constant(hparams.eos)),
        tf.int32)

    src_dataset = data.TextLineDataset(src_file)
    tgt_dataset = data.TextLineDataset(tgt_file)
    src_tgt_dataset = tf.contrib.data.Dataset.zip((src_dataset, tgt_dataset))

    if skip_count is not None:
        src_tgt_dataset = src_tgt_dataset.skip(skip_count)

    src_tgt_dataset = src_tgt_dataset.shuffle(
        output_buffer_size, hparams.random_seed)

    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt: (
            tf.string_split([src]).values, tf.string_split([tgt]).values),
        num_threads=num_threads,
        output_buffer_size=output_buffer_size)

    # Filter zero length input sequences.
    src_tgt_dataset = src_tgt_dataset.filter(
        lambda src, tgt: tf.logical_and(tf.size(src) > 0, tf.size(tgt) > 0))

    if hparams.src_max_len:
        src_tgt_dataset = src_tgt_dataset.map(
            lambda src, tgt: (src[:hparams.src_max_len], tgt),
            num_threads=num_threads,
            output_buffer_size=output_buffer_size)
    if hparams.tgt_max_len:
        src_tgt_dataset = src_tgt_dataset.map(
            lambda src, tgt: (src, tgt[:hparams.tgt_max_len]),
            num_threads=num_threads,
            output_buffer_size=output_buffer_size)
    if hparams.source_reverse:
        src_tgt_dataset = src_tgt_dataset.map(
            lambda src, tgt: (tf.reverse(src, axis=[0]), tgt),
            num_threads=num_threads,
            output_buffer_size=output_buffer_size)
    # Convert the word strings to ids.  Word strings that are not in the
    # vocab get the lookup table's default_value integer.
    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt: (tf.cast(src_vocab_table.lookup(src), tf.int32),
                            tf.cast(tgt_vocab_table.lookup(tgt), tf.int32)),
        num_threads=num_threads, output_buffer_size=output_buffer_size)
    # Create a tgt_input prefixed with <hparams.sos> and a tgt_output suffixed with <hparams.eos>.
    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt: (src,
                            tf.concat(([tgt_sos_id], tgt), 0),
                            tf.concat((tgt, [tgt_eos_id]), 0)),
        num_threads=num_threads, output_buffer_size=output_buffer_size)
    # Add in the word counts.  Subtract one from the target to avoid counting
    # the target_input <hparams.eos> tag (resp. target_output <hparams.sos> tag).
    src_tgt_dataset = src_tgt_dataset.map(
        lambda src, tgt_in, tgt_out: (
            src, tgt_in, tgt_out, tf.size(src), tf.size(tgt_in)),
        num_threads=num_threads,
        output_buffer_size=output_buffer_size)
    # dev infinite
    if mode == tf.contrib.learn.ModeKeys.EVAL:
        src_tgt_dataset.repeat()
    # Bucket by source sequence length (buckets for lengths 0-9, 10-19, ...)
    def batching_func(x):
        return x.padded_batch(
            batch_size,
            # The first three entries are the source and target line rows;
            # these have unknown-length vectors.  The last two entries are
            # the source and target row sizes; these are scalars.
            padded_shapes=(tf.TensorShape([None]),  # src
                        tf.TensorShape([None]),  # tgt_input
                        tf.TensorShape([None]),  # tgt_output
                        tf.TensorShape([]),      # src_len
                        tf.TensorShape([])),     # tgt_len
            # Pad the source and target sequences with hparams.eos tokens.
            # (Though notice we don't generally need to do this since
            # later on we will be masking out calculations past the true sequence.
            padding_values=(src_eos_id,  # src
                            tgt_eos_id,  # tgt_input
                            tgt_eos_id,  # tgt_output
                            0,           # src_len -- unused
                            0))          # tgt_len -- unused
    if hparams.num_buckets > 1:
        def key_func(unused_1, unused_2, unused_3, src_len, tgt_len):
            # Calculate bucket_width by maximum source sequence length.
            # Pairs with length [0, bucket_width) go to bucket 0, length
            # [bucket_width, 2 * bucket_width) go to bucket 1, etc.  Pairs with length
            # over ((num_bucket-1) * bucket_width) words all go into the last bucket.
            if hparams.src_max_len:
                bucket_width = (hparams.src_max_len + hparams.num_buckets - 1) // hparams.num_buckets
            else:
                bucket_width = 10

            # Bucket sentence pairs by the length of their source sentence and target
            # sentence.
            bucket_id = tf.maximum(src_len // bucket_width, tgt_len // bucket_width)
            return tf.to_int64(tf.minimum(hparams.num_buckets, bucket_id))
        def reduce_func(unused_key, windowed_data):
            return batching_func(windowed_data)
        batched_dataset = src_tgt_dataset.group_by_window(
            key_func=key_func, reduce_func=reduce_func, window_size=batch_size)
    else:
        batched_dataset = batching_func(src_tgt_dataset)

    batched_iter = batched_dataset.make_initializable_iterator()
    (src_ids, tgt_input_ids, tgt_output_ids, src_seq_len, tgt_seq_len) = (
        batched_iter.get_next())
    return BatchedInput(
        initializer=batched_iter.initializer,
        source=src_ids,
        target_input=tgt_input_ids,
        target_output=tgt_output_ids,
        source_sequence_length=src_seq_len,
        target_sequence_length=tgt_seq_len)


def input_test():
    pass


if __name__ == '__main__':
    input_test()
