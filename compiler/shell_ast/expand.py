import copy
import logging

from shell_ast.ast_node import *

## TODO: Make this a global variable that is set so that it can be used as a library
import config

################################################################################
# SAFE EXPANSION ANALYSIS
################################################################################

def log(msg: str):
    logging.info(f'Expansion: {msg}')

## This function checks if a word is safe to expand (i.e. if it will 
## not have unpleasant side-effects)
def safe_to_expand(arg_char: ArgChar):
    if isinstance(arg_char, VArgChar): # Variable
        return True
    return False

def guess_arg(arg):
    res = ""
    for arg_char in arg:
        if isinstance(arg_char, CArgChar) \
            or isinstance(arg_char, EArgChar):
            res += chr(arg_char.char)
        else:
            return None
    return res

def safe_arg(arg):
    return all([safe_arg_char(arg_char) for arg_char in arg])

def safe_args(args):
    return all([safe_arg(arg) for arg in args])

def safe_arg_char(arg_char: ArgChar):
    # character, escaped---noop, but safe
    if isinstance(arg_char, CArgChar) \
        or isinstance(arg_char, EArgChar):
        return True
    # tilde --- only reads system state, safe to do early assuming no writes to HOME prior
    elif isinstance(arg_char, TArgChar):
        return True # TODO 2020-11-24 MMG modified variable set? take in/output written vars...
    # arithmetic -- depends on what we have
    elif isinstance(arg_char, AArgChar):
        return safe_arith(arg_char.arg)
    # quoted -- safe if its contents are safe
    elif isinstance(arg_char, QArgChar):
        return safe_arg(arg_char.arg)
    # variables -- safe if the format is safe as are the remaining words
    elif isinstance(arg_char, VArgChar):
        return safe_var(fmt=arg_char.fmt, 
                        null=arg_char.null, 
                        var=arg_char.var, 
                        arg=arg_char.arg)
    # command substitution -- depends on the command
    elif isinstance(arg_char, BArgChar):
        return safe_command(arg_char.node)
    
    raise ValueError("bad object {}, expected one of CETAVQB".format(arg_char))

def safe_var(fmt, null, var, arg):
    if (fmt in ['Normal', 'Length']):
        return True
    elif (fmt in ['Minus', 'Plus', 'TrimR', 'TrimRMax', 'TrimL', 'TrimLMax']):
        return safe_arg(arg)
    elif (fmt in ['Question']):
        return False # TODO 2020-12-10 MMG unless we know `var` is set
    elif (fmt in ['Assign']):
        return False # TODO 2020-11-24 MMG unless we know `var` is set

    raise ValueError("bad parameter format {}".format(fmt))

def safe_arith(arg):
    # operations are safe
    # `+=` and `=` and family are UNSAFE
    # NONPOSIX: `++` and `--` are UNSAFE
    # `op="+=1"; $((x $op))` is UNSAFE

    # to determine safety, we:
    #   (a) check that every arg_char here is safe
    #   (b) pre-parse it symbolically well enough to ensure that no mutating operations occur
    expr = guess_arg(arg)

    if (arg is None):
        # TODO 2020-11-25 MMG symbolic pre-parse?
        return False
    elif ('=' in expr or '++' in expr or '--' in expr):
        # TODO 2020-11-25 MMG false negatives: ==, >=, <=
        return False
    else:
        # it's a concrete string that doesn't have mutation operations in it... go for it!
        return True

safe_cases = {
        "Pipe": (lambda:
                 lambda ast_node: safe_pipe(ast_node)),
        "Command": (lambda:
                    lambda ast_node: safe_simple(ast_node)),
        "And": (lambda:
                lambda ast_node: safe_and_or_semi(ast_node)),
        "Or": (lambda:
               lambda ast_node: safe_and_or_semi(ast_node)),
        "Semi": (lambda:
                 lambda ast_node: safe_and_or_semi(ast_node)),
        "Redir": (lambda:
                  lambda ast_node: safe_redir_subshell(ast_node)),
        "Subshell": (lambda:
                     lambda ast_node: safe_redir_subshell(ast_node)),
        "Background": (lambda:
                       lambda ast_node: safe_background(ast_node)),
        "Defun": (lambda:
                  lambda ast_node: safe_defun(ast_node)),
        "For": (lambda:
                  lambda ast_node: safe_for(ast_node)),
        "While": (lambda:
                  lambda ast_node: safe_while(ast_node)),
        "Case": (lambda:
                  lambda ast_node: safe_case(ast_node)),
        "If": (lambda:
                  lambda ast_node: safe_if(ast_node))
        }

def safe_command(command):
    # TODO 2020-11-24 MMG which commands are safe to run in advance?
    # TODO 2020-11-24 MMG how do we differentiate it being safe to do nested expansions?
    global safe_cases
    return ast_match(command, safe_cases)

def safe_pipe(node):
    return False

safe_commands = ["echo", ":"]

def safe_simple(node: CommandNode):
    # TODO 2020-11-25 check redirs, assignments

    if (len(node.arguments) <= 0):
        return True

    cmd = guess_arg(node.arguments[0])
    if (cmd is None or cmd not in safe_commands):
        return False
    else:
        return safe_args(node.arguments[1:])

def safe_and_or_semi(node):
    return False

def safe_redir_subshell(node):
    return False

def safe_background(node):
    return False

def safe_defun(node):
    return False

def safe_for(node):
    return False

def safe_while(node):
    return False

def safe_case(node):
    return False

def safe_if(node):
    return False

################################################################################
# EARLY EXPANSION
################################################################################

# General approach:
#
# - expand_* functions try to expand the AST
#   + words return a string when it works, raises when it doesn't
#     TODO MMG 2020-12-14 really should return (intermediate?) fields, not a single string
#   + commands just set the structural bits appropriately

# when early expansion detects an error
class EarlyError(RuntimeError):
    def __init__(self, arg):
        self.arg = arg

class StuckExpansion(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info

class ImpureExpansion(RuntimeError):
    def __init__(self, reason, *info):
        self.reason = reason
        self.info = info

class Unimplemented(RuntimeError):
    def __init__(self, msg, ast):
        self.msg = msg
        self.ast = ast

class InvalidVariable(RuntimeError):
    def __init__(self, var, reason):
        self.var = var
        self.reason = reason

## TODO: Figure out if there is a way to batch calls to bash and ask it 
##       to expand everything at once! We would need to make variable lookups asynchronous.
##
## TODO: `config` doesn't need to be passed down since it is imported
def lookup_variable(var, _lookup_config):
    ## If the variable is input arguments then get it from pash_input_args.
    ##
    ## TODO KK PR#246 Do we need to split using IFS or is it always spaces?
    ##
    ## TODO KK PR#246 Maybe instead of this we could do this setup
    ##      once during initialization and leave lookup unaltered?
    ##
    ## TODO MMG this isn't quite adequate: if pash_input_args contains
    ##      spaces, we'll miscount. KK and I wrote a test
    ##      evaluation/tests/interface_tests that's disabled as of PR#246.
    ##
    ##      the right solution here is:
    ##
    ##         - positional arguments get their own field in the
    ##           config---they're not store with ordinary shell
    ##           variables
    ##
    ##         - we save those separately, probably in a separate file
    ##
    ##           ```
    ##           echo pash_argc=$# >pash_positional_args
    ##           for i in $(seq 0 $#)
    ##           do
    ##             echo "pash_arg$i=\"$i\"" >pash_positional_args
    ##           done
    ##           ```
    ##
    ##         - we load these separately. pretty annoying; here's a sketch
    ##
    ##           ```
    ##           cmd="set --"
    ##           for i in $(seq 0 $pash_argc)
    ##           do
    ##             cmd="$cmd \"\$pash_arg$i\""
    ##           done
    ##           eval "$cmd"


    if(var == '@'):
        argument_values = lookup_variable_inner_core('pash_input_args')
        expanded_var = " ".join(argument_values)
    elif(var == '?'):
        expanded_var = lookup_variable_inner('pash_previous_exit_status')
    elif(var == '-'):
        expanded_var = lookup_variable_inner('pash_previous_set_status')
    elif(var == '#'):
        argument_values = lookup_variable_inner_core('pash_input_args')
        expanded_var = str(len(argument_values))
    elif(var.isnumeric() and int(var) >= 1):
        input_args = lookup_variable_inner_core('pash_input_args')
        # split_args = input_args.split()
        index = int(var) - 1
        try:
            expanded_var = input_args[index]
        except:
            ## If there are not enough arguments -u is set we need to raise
            if is_u_set():
                raise StuckExpansion("-u is set and positional argument wasn't set", var)

            expanded_var = ''
    elif(var == '0'):
        expanded_var = lookup_variable_inner('pash_shell_name')
    else:
        ## TODO: We can pull this to expand any string.
        expanded_var = lookup_variable_inner(var)
    
    return None, expanded_var

## Looksup a variable and flattens it if it is an array 
def lookup_variable_inner(varname):
    value = lookup_variable_inner_core(varname)
    if value is not None and not isinstance(value, str):
        ## TODO: This is not handled at the moment (and it is unclear if it should be).
        ##
        ## This is only returned when we are in an array
        raise Unimplemented("Expanded value is not None or a string", (varname, value))
    return value

## Looks up the variable and if it is unset it raises an error
def lookup_variable_inner_core(varname):
    value = lookup_variable_inner_unsafe(varname)
    if value is None and is_u_set():
        raise StuckExpansion("-u is set and variable was unset", varname)
    return value


def lookup_variable_inner_unsafe(varname):
    ## TODO: Is it in there? If we have -u and it is in there.
    _type, value = config.config['shell_variables'].get(varname, [None, None])
    return value

## This function checks if the -u flag is set
def is_u_set():
    ## This variable is set by pash and is exported and therefore will be in the variable file.
    _type, value = config.config['shell_variables']["pash_previous_set_status"]
    # log(f'Previous set status is: {value}')
    return "u" in value


def invalidate_variable(var, reason, config):
    config['shell_variables'][var] = [None, InvalidVariable(var, reason)]
    return config

def try_string(expanded):
    res = ""
    for arg_char in expanded:
        if isinstance(arg_char, CArgChar) \
            or isinstance(arg_char, EArgChar):
            res += chr(val)
        elif isinstance(arg_char, QArgChar):
            # TODO 2020-12-17 fields issues
            res += try_string(arg_char.arg)
        else:
            raise StuckExpansion("left over control code", expanded, arg_char)

    return res

def try_set_variable(var, expanded, config):
    str = try_string(expanded)
    config['shell_variables'][var] = [None, str]

    return config

## TODO: Replace this with an expansion that happens in the bash mirror
##
## TODO: If there is any potential side-effect, exit early
def expand_args(args, config, quoted = False):
    res = []
    for arg in args:
        new = expand_arg(arg, config, quoted = quoted)

        # expanded! add the string in
        res.append(new)

    splitted_args = split_args(res, config)

    return splitted_args

def split_args(args, config):
    _, ifs = lookup_variable("IFS", config)

    if ifs is None:
        ifs = "\n\t "

    ifs = [ord(c) for c in ifs]

    res = []
    for arg in args:
        cur = []

        for c in arg:
            if isinstance(c, CArgChar) and c.char in ifs:
                 # split!
                 if len(cur) > 0: # TODO(mmg): or if val isn't IFS whitespace
                     res.append(cur)
                 cur = []
            else:
                cur.append(c)

        if len(cur) > 0:
            res.append(cur)

    return res

def char_code(c) -> ArgChar:
    if c in "'\\\"()${}[]*?":
        return EArgChar(ord(c))
    else:
        return CArgChar(ord(c))

def expand_arg(arg_chars, config, quoted = False):
    # log(f'expanding arg {arg_chars}")
    # log(f"unparsed_string: {parse.pash_string_of_arg(arg_chars)}")
    res = []
    for arg_char in arg_chars:
        new = expand_arg_char(arg_char, quoted, config)

        if isinstance(new, str):
            res += [char_code(c) for c in list(new)]
        else:
            res.extend(new)

    return res

def expand_arg_char(arg_char: ArgChar, quoted, config):
    if isinstance(arg_char, CArgChar):
        if arg_char.char in ['*', '?', '{', '}', '[', ']'] and not quoted:
            raise Unimplemented("globbing", arg_char)

        return [arg_char]
    elif isinstance(arg_char, EArgChar):
        ## 2021-09-15 MMG Just guessing here
        if arg_char.char in ['*', '?', '{', '}', '[', ']'] and not quoted:
            raise Unimplemented("globbing", arg_char)
        return [arg_char]
    elif isinstance(arg_char, TArgChar):
        val = arg_char.string
        if val is None or val == "" or val == "None":
            _type, val = lookup_variable("HOME", config)

            if isinstance(val, InvalidVariable):
                raise StuckExpansion("HOME invalid for ~", arg_char, val)
            elif val is None:
                return "~"
            else:
                return val
        else:
            # TODO 2020-12-10 getpwnam
            raise Unimplemented("~ with prefix", arg_char)
    elif isinstance(arg_char, AArgChar):
        # TODO 2020-12-10 arithmetic parser and evaluator
        raise Unimplemented("arithmetic", arg_char)
    elif isinstance(arg_char, QArgChar):
        return [QArgChar(expand_arg(arg_char.arg, config, quoted = True))]
        # return [['Q', expand_arg(arg_char.arg, config, quoted = True)]]
    elif isinstance(arg_char, VArgChar):
        return expand_var(fmt=arg_char.fmt, 
                          null=arg_char.null, 
                          var=arg_char.var, 
                          arg=arg_char.arg, 
                          quoted=quoted, 
                          config=config)
    elif isinstance(arg_char, BArgChar):
        # TODO 2020-12-10 run commands?
        raise ImpureExpansion("command substitution", arg_char)
    else:
        raise Unimplemented("weird object", arg_char)

def expand_var(fmt, null, var, arg, quoted, config):
    # TODO 2020-12-10 special variables

    _type, value = lookup_variable(var, config)

    log(f'Var: {var} value: {value}')

    if isinstance(value, InvalidVariable):
        raise StuckExpansion("couldn't expand invalid variable", value)

    if fmt == 'Normal':
        if value is None:
            return ""
        else:
            return value
    elif fmt == 'Length':
        if value is None:
            return "0"
        else:
            return str(len(value))
    elif fmt == 'Minus':
        if value is None or (null and value == ""):
            return expand_arg(arg, config, quoted = quoted)
        else:
            return value
    elif fmt == 'Assign':
        if value is None or (null and value == ""):
            raise ImpureExpansion("assignment format on unset/null variable", value, arg)
#            new = expand_arg(arg, config, quoted = quoted)
#            config = try_set_variable(var, new, config)
#            return new
        else:
            return value
    elif fmt == 'Plus':
        if value is None or (null and value == ""):
            return ""
        else:
            return expand_arg(arg, config, quoted = quoted)
    elif fmt == 'Question':
        if value is None or (null and value == ""):
            # TODO 2020-12-10 more context probably helpful here
            raise EarlyError(expand_arg(arg, config, quoted = quoted))
        else:
            return value
    elif fmt in ['TrimR', 'TrimRMax', 'TrimL', 'TrimLMax']:
        # TODO need patterns
        raise Unimplemented("patterns", [fmt, null, var, arg])
    else:
        raise ValueError("bad parameter format {}".format(fmt))

expand_cases = {
        "Pipe": (lambda config:
                 lambda ast_node: expand_pipe(ast_node, config)),
        "Command": (lambda config:
                    lambda ast_node: expand_simple(ast_node, config)),
        "And": (lambda config:
                lambda ast_node: expand_and_or_semi(ast_node, config)),
        "Or": (lambda config:
               lambda ast_node: expand_and_or_semi(ast_node, config)),
        "Semi": (lambda config:
                 lambda ast_node: expand_and_or_semi(ast_node, config)),
        "Redir": (lambda config:
                  lambda ast_node: expand_redir_subshell(ast_node, config)),
        "Subshell": (lambda config:
                     lambda ast_node: expand_redir_subshell(ast_node, config)),
        "Background": (lambda config:
                       lambda ast_node: expand_background(ast_node, config)),
        "Defun": (lambda config:
                  lambda ast_node: expand_defun(ast_node, config)),
        "For": (lambda config:
                  lambda ast_node: expand_for(ast_node, config)),
        "While": (lambda config:
                  lambda ast_node: expand_while(ast_node, config)),
        "Case": (lambda config:
                  lambda ast_node: expand_case(ast_node, config)),
        "If": (lambda config:
                  lambda ast_node: expand_if(ast_node, config))
        }

def expand_command(command, config):
    # TODO 2020-11-24 MMG which commands are safe to run in advance?
    # TODO 2020-11-24 MMG how do we differentiate it being safe to do nested expansions?
    global expand_cases
    return ast_match(command, expand_cases, config)

def expand_pipe(node, config):
    for i, n in enumerate(node.items):
        # copy environment to simulate subshell (no outer effect)
        node.items[i] = expand_command(n, copy.deepcopy(config))

    return node

def expand_simple(node, config):
    # TODO 2020-11-25 MMG is this the order bash does?
    node.redir_list = expand_redir_list(node.redir_list, config)

    if len(node.assignments) > 0:
        raise ImpureExpansion('assignment', node.assignments)
    
    #settable = dict()
    #
    #for (i, [x, arg]) in enumerate(node.assignments):
    #    exp = expand_arg(arg, config)
    #    node.assignments[i] = [x, exp]
    #
    #    # assignment visibility:
    #    #
    #    # assignments are immediately done when no command...
    #    if len(node.arguments) == 0:
    #        config = try_set_variable(x, exp, config)
    #    else:
    #        # or deferred until later when there is one
    #        settable[x] = exp
    #
    ## once all values are found, _then_ set them before the command
    ## TODO 2020-11-25 if node.arguments[0] is a special builtin, these things are global
    ## if not... then the settings are just for the command, and shouldn't go in the config
    #for (x,exp) in settable:
    #    try_set_variable(x, exp, config)

    node.arguments = expand_args(node.arguments, config)

    return node

def expand_redir_list(redir_list, config):
    for (i, r) in enumerate(redir_list):
        redir_list[i] = expand_redir(r, config)

    return redir_list

def expand_redir(redirection, config):
    redir_type = redirection[0]
    redir_subtype = redirection[1][0]
    stream_id = redirection[1][1]
    file_arg = expand_arg(redirection[1][2], config)

    redirection[1][2] = file_arg
    return redirection

def expand_and_or_semi(node, config):
    node.left_operand = expand_command(node.left_operand, config)
    node.right_operand = expand_command(node.right_operand, config)

    return node

def expand_redir_subshell(node, config):
    # copy environment to simulate subshell (no outer effect)
    node.node = expand_command(node.node, copy.deepcopy(config))

    return node

def expand_background(node, config):
    # copy environment to simulate subshell (no outer effect)
    node.node = expand_command(node.node, copy.deepcopy(config))

    return node

def expand_defun(node, config):
    # TODO 2020-11-24 MMG invalidate postional args
    node.body = expand_command(node.body, copy.deepcopy(config))

    return node

def expand_for(node, config):
    node.argument = expand_arg(node.argument, config)

    # TODO 2020-11-24 if node.argument is fully expanded, we can just unroll the loop
    config = invalidate_variable(node.variable, "variable of for loop", config)
    node.body = expand_command(node.body, config)

    return node

def expand_while(node, config):
    node.test = expand_command(node.test, config)
    node.body = expand_command(node.body, config)

    return node

def expand_case(node, config):
    # TODO 2020-11-24 preprocess scrutinee, each pattern, each case

    raise Unimplemented("case statements", node)

def expand_if(node, config):
    node.cond = expand_command(node.cond, config)
    node.then_b = expand_command(node.then_b, config)
    node.else_b = expand_command(node.else_b, config)

    return node
