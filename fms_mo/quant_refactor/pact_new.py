# Copyright The FMS Model Optimizer Authors
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

"""
PACT quantizer
"""

# Third Party
import torch

# Local
from fms_mo.quant_refactor.base_quant import QuantizerBase, Qscheme
from fms_mo.quant_refactor.base_tensor import (
    PerTensorSTEBase,
    PerTensorSTEBase_PTnative,
)

perTQscheme_default = Qscheme(
    unit="perT",
    symmetric=False,
    Nch=None,
    Ngrp=None,
    single_sided=True,
    qlevel_lowering=False,
)
clip_valn_default = torch.tensor(0.0)
clip_val_default = torch.tensor(8.0)

class PACT_new(QuantizerBase):
    """
    1-sided original PACT
    PACT is only used to quantize activations

    Extends:
        QuantizerBase
    """

    def __init__(
        self,
        num_bits: torch.IntTensor,
        init_clip_valn: torch.FloatTensor = clip_valn_default,
        init_clip_val: torch.FloatTensor = clip_val_default,
        qscheme: Qscheme = perTQscheme_default,
        dequantize: bool = True,
        pact_plus: bool = True,
        **kwargs
    ):
        """
        Initialize PACT quantizer

        Args:
            num_bits (torch.IntTensor): Number of bits for quantization.
            init_clip_valn (torch.FloatTensor, optional): Lower clip value bound. Defaults to 0.0.
            init_clip_val (torch.FloatTensor, optional): Upper clip value bound. Defaults to 8.0.
            qscheme (Qscheme, optional): Quantization scheme.
                Defaults to Qscheme( unit="perT", symmetric=False, Nch=None, Ngrp=None,
                                       single_sided=True, qlevel_lowering=False, ).
            dequantize (bool, optional): Return dequantized or int tensor. Defaults to True.
            pact_plus (bool, optional): Specify using PACT+ quantizer. Defaults to True.
            kwargs.use_PT_native_Qfunc (bool): Use native PT quantizer.  Defaults to False.
        """
        super().__init__(
            num_bits,
            dequantize,
            qscheme,
            use_PT_native_Qfunc=kwargs.get("use_PT_native_Qfunc", False),
        )

        with torch.no_grad():
            self.clip_valn.data *= init_clip_valn  # always 0.0
            self.clip_val.data *= init_clip_val

        self.pact_plus = pact_plus
        self.set_quantizer()

    def set_quantizer(self):
        """
        Set quantizer STE based on current member variables
        """
        if self.use_PT_native_Qfunc:  # PTnative overrides all other options
            self.quantizer = PerTensorSTEBase_PTnative
        else:
            self.quantizer = PACTplusSTE_new if self.pact_plus else PACT_STE_new


class PACT_STE_new(PerTensorSTEBase):
    """
    Single-sided PACT STE

    Extends:
        PerTensorSTEBase: Uses PerTensorSTEBase.forward()
    """

    @staticmethod
    def backward(ctx, grad_output):
        """
        Backward function for PACT

        Args:
            ctx (torch.autograd.Function): Context object.
            grad_output (torch.FloatTensor): Gradient to clip

        Returns:
            [torch.FloatTensor, torch.FloatTensor, None,...,None]: Gradients
        """
        input_tensor, _, _, clip_val, _, _ = ctx.saved_tensors
        grad_input = grad_output.clone()
        grad_input = torch.where(
            input_tensor < 0, torch.zeros_like(grad_input), grad_input
        )
        grad_input = torch.where(
            input_tensor > clip_val, torch.zeros_like(grad_input), grad_input
        )
        grad_alpha = grad_output.clone()
        grad_alpha = torch.where(
            input_tensor < clip_val, torch.zeros_like(grad_alpha), grad_alpha
        )
        grad_alpha = grad_alpha.sum().expand_as(clip_val)
        return grad_input, grad_alpha, None, None, None, None, None


class PACTplusSTE_new(PerTensorSTEBase):
    """
    Single-sided PACT+ STE

    Extends:
        PerTensorSTEBase: Uses PerTensorSTEBase.forward()
    """

    @staticmethod
    def backward(ctx, grad_output):
        """
        Backward function for PACT

        Args:
            ctx (torch.autograd.Function): Context object.
            grad_output (torch.FloatTensor): Gradient to clip

        Returns:
            [torch.FloatTensor, torch.FloatTensor, None,...,None]: Gradients
        """
        input_tensor, n_levels, _, clip_val, scale, _ = ctx.saved_tensors
        z = input_tensor / scale

        # direct compute grad_alpha
        grad_input = grad_output.clone()
        delz = (z - torch.round(z)) / n_levels
        grad_alpha = -grad_output.clone() * delz

        grad_input = torch.where(
            input_tensor <= 0, torch.zeros_like(grad_input), grad_input
        )
        grad_input = torch.where(
            input_tensor >= clip_val, torch.zeros_like(grad_input), grad_input
        )

        grad_alpha = torch.where(
            input_tensor <= 0, torch.zeros_like(grad_alpha), grad_alpha
        )
        grad_alpha = torch.where(input_tensor >= clip_val, grad_output, grad_alpha)

        grad_alpha = grad_alpha.sum().expand_as(clip_val)

        return grad_input, grad_alpha, None, None, None, None, None
