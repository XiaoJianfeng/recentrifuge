"""
Functions directly related with Kraken.

"""

import collections as col
import io
import os
from enum import Enum
from math import log10
from statistics import mean
from typing import Tuple, Counter, Dict, List, NamedTuple

from recentrifuge.config import Filename, Id, Score, Scoring
from recentrifuge.config import gray, red, green, yellow, blue, magenta
from recentrifuge.stats import SampleStats


class GenericType(Enum):
    """Enumeration with options for the type of supported generic file."""
    CSV = 0
    TSV = 1

    def __str__(self):
        return f'{str(self.name)}'


class GenericFormat(object):

    def __init__(self, format: str):

        def print_error(specifier):
            """GenericFormat constructor: print an informative error message"""
            print(red('ERROR!'), 'Generic --format string malformed:',
                  blue(specifier), '\n\tPlease rerun with --help for details.')

        blocks: List[str] = [fld.strip() for fld in format.split(',')]
        fmt: Dict[str, str] = {pair.split(':')[0].strip(): pair.split(':')[1].strip()
                               for pair in blocks}
        try:
            typ = fmt['TYP']
        except KeyError:
            print_error('TYPe field is mandatory.')
            raise
        try:
            self.typ: GenericType = GenericType[typ.upper()]
        except KeyError:
            print_error('Unknown file TYPe, valid options are ' +
                        ' or '.join([str(t) for t in GenericType]))
        try:
            self.tid: int = int(fmt['TID'])
        except KeyError:
            print_error('TaxID field is mandatory.')
            raise
        except ValueError:
            print_error('TaxID field is an integer number of column.')
            raise
        try:
            self.len: int = int(fmt['LEN'])
        except KeyError:
            print_error('LENgth field is mandatory.')
            raise
        except ValueError:
            print_error('LENgth field is an integer number of column.')
            raise
        try:
            self.sco: int = int(fmt['SCO'])
        except KeyError:
            print_error('SCOre field is mandatory.')
            raise
        except ValueError:
            print_error('SCOre field is an integer number of column.')
            raise
        try:
            self.unc: Id = Id(fmt['UNC'])
        except KeyError:
            print_error('UNClassified field is mandatory.')
            raise

    def __str__(self):
        return (f'Generic format = TYP:{self.typ}, TID:{self.tid}, '
                f'LEN:{self.len}, SCO:{self.sco}, UNC:{self.unc}.')


def read_generic_output(output_file: Filename,
                        format: GenericFormat,
                        minscore: Score = None,
                        ) -> Tuple[str, SampleStats,
                                   Counter[Id], Dict[Id, Score]]:
    """
    Read Kraken output file

    Args:
        output_file: output file name
        scoring: type of scoring to be applied (see Scoring class)
        minscore: minimum confidence level for the classification

    Returns:
        log string, statistics, abundances counter, scores dict

    """
    output: io.StringIO = io.StringIO(newline='')
    all_scores: Dict[Id, List[Score]] = {}
    all_kmerel: Dict[Id, List[Score]] = {}
    all_length: Dict[Id, List[int]] = {}
    num_read: int = 0
    nt_read: int = 0
    num_uncl: int = 0
    error_read: int = -1
    output.write(gray(f'Loading output file {output_file}... '))
    try:
        with open(output_file, 'r') as file:
            # Check number of cols in header
            header = file.readline().split('\t')
            if len(header) != 5:
                print(red('\nERROR! ') + 'Kraken output format of ',
                      yellow(f'"{output_file}"'), 'not supported.')
                print(magenta('Expected:'),
                      'C/U, ID, taxid, length, list of mappings')
                print(magenta('Found:'), '\t'.join(header), end='')
                print(blue('HINT:'), 'Use Kraken or Kraken2 direct output.')
                raise Exception('Unsupported file format. Aborting.')
            for raw_line in file:
                try:
                    output_line = raw_line.strip()
                    (_clas, _label, _tid, _length,
                     _maps) = output_line.split('\t')
                except ValueError:
                    print(yellow('Error'), f' parsing line: ({output_line}) '
                                           f'in {output_file}. Ignoring line!')
                    error_read = num_read + 1
                    continue
                try:
                    length: int = sum(map(int, _length.split('|')))
                    num_read += 1
                    nt_read += length
                    if _clas == UNCLASSIFIED:  # Just count unclassified reads
                        num_uncl += 1
                        continue
                    tid: Id = Id(_tid)
                    maps: List[str] = _maps.split()
                    try:
                        maps.remove('|:|')
                    except ValueError:
                        pass
                    mappings: Counter[Id] = col.Counter()
                    for pair in maps:
                        couple: List[str] = pair.split(':')
                        mappings[Id(couple[0])] += int(couple[1])
                    # From Kraken score get "single hit equivalent length"
                    shel: Score = Score(mappings[tid] + K_MER_SIZE)
                    score: Score = Score(mappings[tid] / sum(mappings.values())
                                         * 100)  # % relative to all k-mers
                except ValueError:
                    print(yellow('Error'), 'parsing elements of'
                                           f' line: ({output_line}) '
                                           f'in {output_file}. Ignoring line!')
                    error_read = num_read + 1
                    continue
                if minscore is not None:  # Decide if ignore read if low score
                    if scoring is Scoring.KRAKEN:
                        if score < minscore:
                            continue
                    else:
                        if shel < minscore:
                            continue
                try:
                    all_scores[tid].append(shel)
                except KeyError:
                    all_scores[tid] = [shel, ]
                try:
                    all_kmerel[tid].append(score)
                except KeyError:
                    all_kmerel[tid] = [score, ]
                try:
                    all_length[tid].append(length)
                except KeyError:
                    all_length[tid] = [length, ]
    except FileNotFoundError:
        raise Exception(red('\nERROR! ') + f'Cannot read "{output_file}"')
    if error_read == num_read + 1:  # Check if error in last line: truncated!
        print(yellow('Warning!'), f'{output_file} seems truncated!')
    counts: Counter[Id] = col.Counter({tid: len(all_scores[tid])
                                       for tid in all_scores})
    output.write(green('OK!\n'))
    if num_read == 0:
        raise Exception(red('\nERROR! ')
                        + f'Cannot read any sequence from "{output_file}"')
    filt_seqs: int = sum([len(scores) for scores in all_scores.values()])
    if filt_seqs == 0:
        raise Exception(red('\nERROR! ') + 'No sequence passed the filter!')
    # Get statistics
    stat: SampleStats = SampleStats(
        minscore=minscore, nt_read=nt_read, lens=all_length,
        scores=all_scores, scores2=all_kmerel,
        seq_read=num_read, seq_unclas=num_uncl, seq_filt=filt_seqs
    )
    # Output statistics
    output.write(gray('  Seqs read: ') + f'{stat.seq.read:_d}\t' + gray('[')
                 + f'{stat.nt_read}' + gray(']\n'))
    output.write(gray('  Seqs clas: ') + f'{stat.seq.clas:_d}\t' + gray('(') +
                 f'{stat.get_unclas_ratio():.2%}' + gray(' unclassified)\n'))
    output.write(gray('  Seqs pass: ') + f'{stat.seq.filt:_d}\t' + gray('(') +
                 f'{stat.get_reject_ratio():.2%}' + gray(' rejected)\n'))
    output.write(gray('  Scores SHEL: min = ') + f'{stat.sco.mini:.1f},' +
                 gray(' max = ') + f'{stat.sco.maxi:.1f},' +
                 gray(' avr = ') + f'{stat.sco.mean:.1f}\n')
    output.write(gray('  Coverage(%): min = ') + f'{stat.sco2.mini:.1f},' +
                 gray(' max = ') + f'{stat.sco2.maxi:.1f},' +
                 gray(' avr = ') + f'{stat.sco2.mean:.1f}\n')
    output.write(gray('  Read length: min = ') + f'{stat.len.mini},' +
                 gray(' max = ') + f'{stat.len.maxi},' +
                 gray(' avr = ') + f'{stat.len.mean}\n')
    output.write(f'  {stat.num_taxa}' + gray(f' taxa with assigned reads\n'))
    # Select score output
    out_scores: Dict[Id, Score]
    if scoring is Scoring.SHEL:
        out_scores = {tid: Score(mean(all_scores[tid])) for tid in all_scores}
    elif scoring is Scoring.KRAKEN:
        out_scores = {tid: Score(mean(all_kmerel[tid])) for tid in all_kmerel}
    elif scoring is Scoring.LENGTH:
        out_scores = {tid: Score(mean(all_length[tid])) for tid in all_length}
    elif scoring is Scoring.LOGLENGTH:
        out_scores = {tid: Score(log10(mean(all_length[tid])))
                      for tid in all_length}
    elif scoring is Scoring.NORMA:
        scores: Dict[Id, Score] = {tid: Score(mean(all_scores[tid]))
                                   for tid in all_scores}
        lengths: Dict[Id, Score] = {tid: Score(mean(all_length[tid]))
                                    for tid in all_length}
        out_scores = {tid: Score(scores[tid] / lengths[tid] * 100)
                      for tid in scores}
    else:
        print(red('ERROR!'), f'kraken: Unsupported Scoring "{scoring}"')
        raise Exception('Unsupported scoring')
    # Return
    return output.getvalue(), stat, counts, out_scores


def select_kraken_inputs(krakens: List[Filename],
                         ext: str = '.krk') -> None:
    """Search for Kraken files to analyze"""
    dir_name = krakens[0]
    krakens.clear()
    with os.scandir(dir_name) as dir_entry:
        for fil in dir_entry:
            if not fil.name.startswith('.') and fil.name.endswith(ext):
                if dir_name != '.':
                    krakens.append(Filename(os.path.join(dir_name, fil.name)))
                else:  # Avoid sample names starting with just the dot
                    krakens.append(Filename(fil.name))
    krakens.sort()
    print(gray(f'Kraken {ext} files to analyze:'), krakens)