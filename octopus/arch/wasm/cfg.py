from octopus.api.function import Function
from octopus.api.basicblock import BasicBlock
from octopus.api.edge import (Edge,
                              EDGE_UNCONDITIONAL,
                              EDGE_CONDITIONAL_TRUE, EDGE_CONDITIONAL_FALSE,
                              EDGE_FALLTHROUGH, EDGE_CALL)
from octopus.api.cfg import CFG

from octopus.arch.wasm.analyzer import WasmModuleAnalyzer
from octopus.arch.wasm.disassembler import WasmDisassembler
from octopus.arch.wasm.format import (format_func_name,
                                      format_bb_name)

import binascii
import logging
log = logging.getLogger(__name__)
log.setLevel(level=logging.WARNING)


def enum_func(module_bytecode):
    ''' return a list of Function
        see:: octopus.api.function
    '''
    functions = list()
    analyzer = WasmModuleAnalyzer(module_bytecode)

    protos = analyzer.func_prototypes
    import_len = len(analyzer.imports_func)

    for idx, code in enumerate(analyzer.codes):
        # get corresponding function prototype
        name, param_str, return_str = protos[import_len + idx]

        name = format_func_name(name, param_str, return_str)
        instructions = WasmDisassembler().disassemble(code)
        cur_function = Function(0, instructions[0], name=name)
        cur_function.instructions = instructions

        functions.append(cur_function)
    return functions


def enum_func_call_edges(functions, len_imports):
    ''' return a list of tuple with
        (index_func_node_from, index_func_node_to)
    '''
    call_edges = list()

    # iterate over functions
    for index, func in enumerate(functions):
        node_from = len_imports + index
        # iterates over instruction
        for inst in func.instructions:
            # detect if inst is a call instructions
            if inst.is_call:
                log.info('%s', inst.operand_interpretation)
                if inst.name == "call":
                    # only get the import_id
                    node_to = int(inst.operand_interpretation.split(' ')[1])
                # The `call_indirect` operator takes a list of function arguments and as the last operand the index into the table.
                elif inst.name == "call_indirect":
                    # the last operand is the index on the table
                    node_to = int(inst.operand_interpretation.split(',')[-1].split(' ')[-1])
                call_edges.append((node_from, node_to))

    return call_edges


def enum_blocks_edges(function_id, instructions):

    """
    Return a list of basicblock after
    statically parsing given instructions
    """

    basicblocks = list()
    edges = list()

    branches = []
    xrefs = []

    intent = 0
    blocks_tmp = []
    blocks_list = []
    # remove last instruction that is 'end' for the funtion
    tt = instructions[:-1]
    for index, inst in enumerate(tt):

        if inst.is_block_terminator:
            start, name = blocks_tmp.pop()
            blocks_list.append((intent, start, inst.offset_end, name))
            intent -= 1
        if inst.is_block_starter:  # in ['block', 'loop']:
            blocks_tmp.append((inst.offset, inst.name))
            intent += 1
        if inst.is_branch:
            branches.append((intent, inst))

    blocks_list = sorted(blocks_list, key=lambda tup: tup[1])

    for depth, inst in branches:
        d2 = int(inst.operand_interpretation.split(' ')[1])
        rep = next(((i, s, e, n) for i, s, e, n in blocks_list if (i == (depth - d2) and s < inst.offset and e > inst.offset_end)), None)
        if rep:
            i, start, end, name = rep
            if name == 'loop':
                value = start  # else name == 'block'
            elif name == 'block':
                value = end
            else:
                value = None
            inst.xref = value
            xrefs.append(value)

    # remove "block" instruction - not usefull graphicaly
    # instructions = [x for x in instructions if x.name not in ['block', 'loop']]

    # enumerate blocks

    new_block = True

    for index, inst in enumerate(instructions):

        # creation of a block
        if new_block:
            block = BasicBlock(inst.offset,
                               inst,
                               name=format_bb_name(function_id, inst.offset))
            new_block = False

        # add current instruction to the basicblock
        block.instructions.append(inst)

        # next instruction is a jump target
        if index < (len(instructions) - 1) and \
           instructions[index + 1].offset in xrefs:
            new_block = True
        # absolute jump - br
        elif inst.is_branch_unconditional:
            new_block = True
        # conditionnal jump - br_if
        elif inst.is_branch_conditional:
            new_block = True
        # end of a block
        elif index < (len(instructions) - 1) and \
                inst.name in ['end']:  # is_block_terminator
            new_block = True
        elif index < (len(instructions) - 1) and \
                instructions[index + 1].name == 'else':  # is_block_terminator
            new_block = True
        # start of a block
        elif index < (len(instructions) - 1) and \
                instructions[index + 1].is_block_starter:
            new_block = True
        # last instruction of the bytecode
        elif inst.offset == instructions[-1].offset:
            new_block = True

        if new_block:
            block.end_offset = inst.offset_end
            block.end_instr = inst
            basicblocks.append(block)
            new_block = True

    # TODO: detect and remove end instruction that end loop

    # enumerate edges
    for index, block in enumerate(basicblocks):
        # get the last instruction
        inst = block.end_instr

        # unconditional jump - br
        if inst.is_branch_unconditional:
            edges.append(Edge(block.name, format_bb_name(function_id, inst.xref), EDGE_UNCONDITIONAL))
        # conditionnal jump - br_if
        elif inst.is_branch_conditional:
            if inst.name == 'if':
                edges.append(Edge(block.name, format_bb_name(function_id, inst.offset_end + 1), EDGE_CONDITIONAL_TRUE))
                # if 'else' in [i.name for i in basicblocks[index + 1].instructions]:
                #    edges.append(Edge(block.name, format_bb_name(function_id, basicblocks[index + 2].start_instr.offset), EDGE_CONDITIONAL_FALSE))
                edges.append(Edge(block.name, format_bb_name(function_id, basicblocks[index + 2].start_instr.offset), EDGE_CONDITIONAL_FALSE)) 
            else:
                edges.append(Edge(block.name, format_bb_name(function_id, inst.xref), EDGE_CONDITIONAL_TRUE))
                edges.append(Edge(block.name, format_bb_name(function_id, inst.offset_end + 1), EDGE_CONDITIONAL_FALSE))
        elif inst.offset != instructions[-1].offset:
            # EDGE_FALLTHROUGH
            edges.append(Edge(block.name, format_bb_name(function_id, inst.offset_end + 1), EDGE_FALLTHROUGH))

    # prevent duplicate edges
    edges = list(set(edges))
    return basicblocks, edges


class WasmCFG(CFG):
    """
    TODO: fix some CFG issue related to block/end/if/else/end instructions
    """
    def __init__(self, module_bytecode, static_analysis=True):

        if isinstance(module_bytecode, str):
            self.module_bytecode = binascii.unhexlify(module_bytecode)
        else:
            self.module_bytecode = module_bytecode
        self.static_analysis = static_analysis
        self.analyzer = None

        self.functions = list()
        self.basicblocks = list()
        self.edges = list()

        if self.static_analysis:
            self.analyzer = WasmModuleAnalyzer(self.module_bytecode)
            self.run_static_analysis()

    def run_static_analysis(self):
        self.functions = enum_func(self.module_bytecode)

        for idx, func in enumerate(self.functions):
            func.basicblocks, edges = enum_blocks_edges(idx, func.instructions)
            # all bb name are unique so we can create global bb & edge list
            self.basicblocks += func.basicblocks
            self.edges += edges

    def get_functions_call_edges(self):

        nodes = list()
        edges = list()

        if not self.analyzer:
            self.analyzer = WasmModuleAnalyzer(self.module_bytecode)
        if not self.functions:
            self.functions = enum_func(self.module_bytecode)

        # create nodes
        for name, param_str, return_str in self.analyzer.func_prototypes:
            nodes.append(format_func_name(name, param_str, return_str))

        log.info('nodes: %s', nodes)

        # create edges
        tmp_edges = enum_func_call_edges(self.functions,
                                         len(self.analyzer.imports_func))

        # tmp_edges = [(node_from, node_to), (...), ...]
        for node_from, node_to in tmp_edges:
            # node_from
            name, param, ret = self.analyzer.func_prototypes[node_from]
            from_final = format_func_name(name, param, ret)
            # node_to
            name, param, ret = self.analyzer.func_prototypes[node_to]
            to_final = format_func_name(name, param, ret)
            edges.append(Edge(from_final, to_final, EDGE_CALL))
        log.info('edges: %s', edges)

        return (nodes, edges)

    def show(self):
        print("len func = %d" % len(self.functions))
        print("len edges = %d" % len(self.edges))
