import sys
import traceback
import execution
import nodes

# Castable types recognized by ezXY stuff + dropdown handling
NUMBER_TYPES = ["FLOAT", "INT", "NUMBER", "COMBO"]

# Keep original
_validate_inputs = execution.validate_inputs

def validate_inputs(prompt, item, validated, *args, **kwargs):
    """
    ezXY patched validate_inputs.

    Strategy:
    - Try ezXY's custom behavior first.
    - If ezXY hits a newer/unknown schema (like dicts where strings are expected),
      fall back to ComfyUI's original validate_inputs so the prompt can run.
    """
    try:
        unique_id = item
        if unique_id in validated:
            return validated[unique_id]

        inputs = prompt[unique_id]['inputs']
        class_type = prompt[unique_id]['class_type']
        obj_class = nodes.NODE_CLASS_MAPPINGS[class_type]

        class_inputs = obj_class.INPUT_TYPES()
        required_inputs = class_inputs['required']

        errors = []
        valid = True

        for x in required_inputs:
            if x not in inputs:
                error = {
                    "type": "required_input_missing",
                    "message": "Required input is missing",
                    "details": f"{x}",
                    "extra_info": {"input_name": x}
                }
                errors.append(error)
                continue

            val = inputs[x]
            info = required_inputs[x]
            type_input = info[0]

            # Linked input
            if isinstance(val, list):
                if len(val) != 2:
                    error = {
                        "type": "bad_linked_input",
                        "message": "Bad linked input, must be a length-2 list of [node_id, slot_index]",
                        "details": f"{x}",
                        "extra_info": {
                            "input_name": x,
                            "input_config": info,
                            "received_value": val
                        }
                    }
                    errors.append(error)
                    continue

                o_id = val[0]
                o_class_type = prompt[o_id]['class_type']
                rtypes = nodes.NODE_CLASS_MAPPINGS[o_class_type].RETURN_TYPES

                # Custom verification: allow numeric-ish mixing
                if isinstance(type_input, list):
                    type_input = "COMBO"

                if rtypes[val[1]] not in NUMBER_TYPES or type_input not in [*NUMBER_TYPES, "STRING"]:
                    if rtypes[val[1]] != type_input:
                        received_type = rtypes[val[1]]
                        details = f"{x}, {received_type} != {type_input}"
                        error = {
                            "type": "return_type_mismatch",
                            "message": "Return type mismatch between linked nodes",
                            "details": details,
                            "extra_info": {
                                "input_name": x,
                                "input_config": info,
                                "received_type": received_type,
                                "linked_node": val
                            }
                        }
                        errors.append(error)
                        continue

                try:
                    r = validate_inputs(prompt, o_id, validated, *args, **kwargs)
                    if r[0] is False:
                        valid = False
                        continue
                except Exception as ex:
                    typ, _, tb = sys.exc_info()
                    valid = False
                    exception_type = f"{typ.__module__}.{typ.__name__}" if typ else "Exception"
                    reasons = [{
                        "type": "exception_during_inner_validation",
                        "message": "Exception when validating inner node",
                        "details": str(ex),
                        "extra_info": {
                            "input_name": x,
                            "input_config": info,
                            "exception_message": str(ex),
                            "exception_type": exception_type,
                            "traceback": traceback.format_tb(tb) if tb else [],
                            "linked_node": val
                        }
                    }]
                    validated[o_id] = (False, reasons, o_id)
                    continue

            # Literal input
            else:
                try:
                    if type_input == "INT":
                        val = int(val)
                        inputs[x] = val
                    if type_input == "FLOAT":
                        val = float(val)
                        inputs[x] = val
                    if type_input == "STRING":
                        val = str(val)
                        inputs[x] = val
                except Exception as ex:
                    error = {
                        "type": "invalid_input_type",
                        "message": f"Failed to convert an input value to a {type_input} value",
                        "details": f"{x}, {val}, {ex}",
                        "extra_info": {
                            "input_name": x,
                            "input_config": info,
                            "received_value": val,
                            "exception_message": str(ex)
                        }
                    }
                    errors.append(error)
                    continue

                if len(info) > 1:
                    if "min" in info[1] and val < info[1]["min"]:
                        error = {
                            "type": "value_smaller_than_min",
                            "message": "Value {} smaller than min of {}".format(val, info[1]["min"]),
                            "details": f"{x}",
                            "extra_info": {
                                "input_name": x,
                                "input_config": info,
                                "received_value": val,
                            }
                        }
                        errors.append(error)
                        continue
                    if "max" in info[1] and val > info[1]["max"]:
                        error = {
                            "type": "value_bigger_than_max",
                            "message": "Value {} bigger than max of {}".format(val, info[1]["max"]),
                            "details": f"{x}",
                            "extra_info": {
                                "input_name": x,
                                "input_config": info,
                                "received_value": val,
                            }
                        }
                        errors.append(error)
                        continue

                if hasattr(obj_class, "VALIDATE_INPUTS"):
                    # ezXY change: use execution.get_input_data
                    input_data_all = execution.get_input_data(inputs, obj_class, unique_id)

                    # Use patched map_node_over_list (if present) just like your original code did
                    # but call it by name (global) to respect the monkey patch section below.
                    retlist = map_node_over_list(obj_class, input_data_all, "VALIDATE_INPUTS", *args, **kwargs)
                    for i, r in enumerate(retlist):
                        if r is not True:
                            details = f"{x}"
                            if r is not False:
                                details += f" - {str(r)}"
                            error = {
                                "type": "custom_validation_failed",
                                "message": "Custom validation failed for node",
                                "details": details,
                                "extra_info": {
                                    "input_name": x,
                                    "input_config": info,
                                    "received_value": val,
                                }
                            }
                            errors.append(error)
                            continue
                else:
                    # Dropdown/list validation
                    if isinstance(type_input, list):
                        if val not in type_input:
                            input_config = info
                            list_info = ""

                            if len(type_input) > 20:
                                list_info = f"(list of length {len(type_input)})"
                                input_config = None
                            else:
                                list_info = str(type_input)

                            error = {
                                "type": "value_not_in_list",
                                "message": "Value not in list",
                                "details": f"{x}: '{val}' not in {list_info}",
                                "extra_info": {
                                    "input_name": x,
                                    "input_config": input_config,
                                    "received_value": val,
                                }
                            }
                            errors.append(error)
                            continue

        if len(errors) > 0 or valid is not True:
            ret = (False, errors, unique_id)
        else:
            ret = (True, [], unique_id)

        validated[unique_id] = ret
        return ret

    except Exception:
        # Fail open: if ezXY validation can't handle a newer schema, defer to ComfyUI core
        return _validate_inputs(prompt, item, validated, *args, **kwargs)

# Put the edited code back where we found it
execution.validate_inputs = validate_inputs
print("validate_inputs() from execution.py patched by ezXY.")
